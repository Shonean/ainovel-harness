"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Sequence = void 0;
const Container_1 = require("../../../../engine/Container");
const ControlCommand_1 = require("../../../../engine/ControlCommand");
const Divert_1 = require("../../../../engine/Divert");
const Value_1 = require("../../../../engine/Value");
const NativeFunctionCall_1 = require("../../../../engine/NativeFunctionCall");
const Object_1 = require("../Object");
const SequenceDivertToResolve_1 = require("./SequenceDivertToResolve");
const SequenceType_1 = require("./SequenceType");
const Weave_1 = require("../Weave");
class Sequence extends Object_1.ParsedObject {
    constructor(elementContentLists, sequenceType) {
        super();
        this.sequenceType = sequenceType;
        this._sequenceDivertsToResolve = [];
        // Generate runtime code that looks like:
        //
        //   chosenIndex = MIN(sequence counter, num elements) e.g. for "Stopping"
        //   if chosenIndex == 0, divert to s0
        //   if chosenIndex == 1, divert to s1  [etc]
        //
        //   - s0:
        //      <content for sequence element>
        //      divert to no-op
        //   - s1:
        //      <content for sequence element>
        //      divert to no-op
        //   - s2:
        //      empty branch if using "once"
        //      divert to no-op
        //
        //    no-op
        //
        this.GenerateRuntimeObject = () => {
            const container = new Container_1.Container();
            container.visitsShouldBeCounted = true;
            container.countingAtStartOnly = true;
            this._sequenceDivertsToResolve = [];
            // Get sequence read count
            container.AddContent(ControlCommand_1.ControlCommand.EvalStart());
            container.AddContent(ControlCommand_1.ControlCommand.VisitIndex());
            const once = (this.sequenceType & SequenceType_1.SequenceType.Once) > 0;
            const cycle = (this.sequenceType & SequenceType_1.SequenceType.Cycle) > 0;
            const stopping = (this.sequenceType & SequenceType_1.SequenceType.Stopping) > 0;
            const shuffle = (this.sequenceType & SequenceType_1.SequenceType.Shuffle) > 0;
            let seqBranchCount = this.sequenceElements.length;
            if (once) {
                seqBranchCount += 1;
            }
            // Chosen sequence index:
            //  - Stopping: take the MIN(read count, num elements - 1)
            //  - Once: take the MIN(read count, num elements)
            //    (the last one being empty)
            if (stopping || once) {
                //var limit = stopping ? seqBranchCount-1 : seqBranchCount;
                container.AddContent(new Value_1.IntValue(seqBranchCount - 1));
                container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName("MIN"));
            }
            else if (cycle) {
                // - Cycle: take (read count % num elements)
                container.AddContent(new Value_1.IntValue(this.sequenceElements.length));
                container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName("%"));
            }
            // Shuffle
            if (shuffle) {
                // Create point to return to when sequence is complete
                const postShuffleNoOp = ControlCommand_1.ControlCommand.NoOp();
                // When visitIndex == lastIdx, we skip the shuffle
                if (once || stopping) {
                    // if( visitIndex == lastIdx ) -> skipShuffle
                    const lastIdx = stopping
                        ? this.sequenceElements.length - 1
                        : this.sequenceElements.length;
                    container.AddContent(ControlCommand_1.ControlCommand.Duplicate());
                    container.AddContent(new Value_1.IntValue(lastIdx));
                    container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName("=="));
                    const skipShuffleDivert = new Divert_1.Divert();
                    skipShuffleDivert.isConditional = true;
                    container.AddContent(skipShuffleDivert);
                    this.AddDivertToResolve(skipShuffleDivert, postShuffleNoOp);
                }
                // This one's a bit more complex! Choose the index at runtime.
                let elementCountToShuffle = this.sequenceElements.length;
                if (stopping) {
                    elementCountToShuffle -= 1;
                }
                container.AddContent(new Value_1.IntValue(elementCountToShuffle));
                container.AddContent(ControlCommand_1.ControlCommand.SequenceShuffleIndex());
                if (once || stopping) {
                    container.AddContent(postShuffleNoOp);
                }
            }
            container.AddContent(ControlCommand_1.ControlCommand.EvalEnd());
            // Create point to return to when sequence is complete
            const postSequenceNoOp = ControlCommand_1.ControlCommand.NoOp();
            // Each of the main sequence branches, and one extra empty branch if
            // we have a "once" sequence.
            for (let elIndex = 0; elIndex < seqBranchCount; elIndex += 1) {
                // This sequence element:
                //  if( chosenIndex == this index ) divert to this sequence element
                // duplicate chosen sequence index, since it'll be consumed by "=="
                container.AddContent(ControlCommand_1.ControlCommand.EvalStart());
                container.AddContent(ControlCommand_1.ControlCommand.Duplicate());
                container.AddContent(new Value_1.IntValue(elIndex));
                container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName("=="));
                container.AddContent(ControlCommand_1.ControlCommand.EvalEnd());
                // Divert branch for this sequence element
                const sequenceDivert = new Divert_1.Divert();
                sequenceDivert.isConditional = true;
                container.AddContent(sequenceDivert);
                let contentContainerForSequenceBranch;
                // Generate content for this sequence element
                if (elIndex < this.sequenceElements.length) {
                    const el = this.sequenceElements[elIndex];
                    contentContainerForSequenceBranch =
                        el.runtimeObject;
                }
                else {
                    // Final empty branch for "once" sequences
                    contentContainerForSequenceBranch = new Container_1.Container();
                }
                contentContainerForSequenceBranch.name = `s${elIndex}`;
                contentContainerForSequenceBranch.InsertContent(ControlCommand_1.ControlCommand.PopEvaluatedValue(), 0);
                // When sequence element is complete, divert back to end of sequence
                const seqBranchCompleteDivert = new Divert_1.Divert();
                contentContainerForSequenceBranch.AddContent(seqBranchCompleteDivert);
                container.AddToNamedContentOnly(contentContainerForSequenceBranch);
                // Save the diverts for reference resolution later (in ResolveReferences)
                this.AddDivertToResolve(sequenceDivert, contentContainerForSequenceBranch);
                this.AddDivertToResolve(seqBranchCompleteDivert, postSequenceNoOp);
            }
            container.AddContent(postSequenceNoOp);
            return container;
        };
        this.AddDivertToResolve = (divert, targetContent) => {
            this._sequenceDivertsToResolve.push(new SequenceDivertToResolve_1.SequenceDivertToResolve(divert, targetContent));
        };
        this.sequenceType = sequenceType;
        this.sequenceElements = [];
        for (const elementContentList of elementContentLists) {
            const contentObjs = elementContentList.content;
            let seqElObject = null;
            // Don't attempt to create a weave for the sequence element
            // if the content list is empty. Weaves don't like it!
            if (contentObjs === null || contentObjs.length === 0) {
                seqElObject = elementContentList;
            }
            else {
                seqElObject = new Weave_1.Weave(contentObjs);
            }
            this.sequenceElements.push(seqElObject);
            this.AddContent(seqElObject);
        }
    }
    get typeName() {
        return "Sequence";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        for (const toResolve of this._sequenceDivertsToResolve) {
            toResolve.divert.targetPath = toResolve.targetContent.path;
        }
    }
}
exports.Sequence = Sequence;
//# sourceMappingURL=Sequence.js.map