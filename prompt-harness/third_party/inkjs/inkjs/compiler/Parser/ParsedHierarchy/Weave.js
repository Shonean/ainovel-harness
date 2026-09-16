"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Weave = void 0;
const AuthorWarning_1 = require("./AuthorWarning");
const Choice_1 = require("./Choice");
const Conditional_1 = require("./Conditional/Conditional");
const ConstantDeclaration_1 = require("./Declaration/ConstantDeclaration");
const Container_1 = require("../../../engine/Container");
const Divert_1 = require("./Divert/Divert");
const Divert_2 = require("../../../engine/Divert");
const DivertTarget_1 = require("./Divert/DivertTarget");
const FlowBase_1 = require("./Flow/FlowBase");
const Gather_1 = require("./Gather/Gather");
const GatherPointToResolve_1 = require("./Gather/GatherPointToResolve");
const Object_1 = require("./Object");
const Sequence_1 = require("./Sequence/Sequence");
const Text_1 = require("./Text");
const TunnelOnwards_1 = require("./TunnelOnwards");
const VariableAssignment_1 = require("./Variable/VariableAssignment");
const TypeAssertion_1 = require("../../../engine/TypeAssertion");
// Used by the FlowBase when constructing the weave flow from
// a flat list of content objects.
class Weave extends Object_1.ParsedObject {
    // Containers can be chained as multiple gather points
    // get created as the same indentation level.
    // rootContainer is always the first in the chain, while
    // currentContainer is the latest.
    get rootContainer() {
        if (!this._rootContainer) {
            this._rootContainer = this.GenerateRuntimeObject();
        }
        return this._rootContainer;
    }
    get namedWeavePoints() {
        return this._namedWeavePoints;
    }
    get lastParsedSignificantObject() {
        if (this.content.length === 0) {
            return null;
        }
        // Don't count extraneous newlines or VAR/CONST declarations,
        // since they're "empty" statements outside of the main flow.
        let lastObject = null;
        for (let ii = this.content.length - 1; ii >= 0; --ii) {
            lastObject = this.content[ii];
            let lastText = (0, TypeAssertion_1.asOrNull)(lastObject, Text_1.Text);
            if (lastText && lastText.text === "\n") {
                continue;
            }
            if (this.IsGlobalDeclaration(lastObject)) {
                continue;
            }
            break;
        }
        const lastWeave = (0, TypeAssertion_1.asOrNull)(lastObject, Weave);
        if (lastWeave) {
            lastObject = lastWeave.lastParsedSignificantObject;
        }
        return lastObject;
    }
    constructor(cont, indentIndex = -1) {
        super();
        // Keep track of previous weave point (Choice or Gather)
        // at the current indentation level:
        //  - to add ordinary content to be nested under it
        //  - to add nested content under it when it's indented
        //  - to remove it from the list of loose ends when
        //     - it has indented content since it's no longer a loose end
        //     - it's a gather and it has a choice added to it
        this.previousWeavePoint = null;
        this.addContentToPreviousWeavePoint = false;
        // Used for determining whether the next Gather should auto-enter
        this.hasSeenChoiceInSection = false;
        this.currentContainer = null;
        this._unnamedGatherCount = 0;
        this._choiceCount = 0;
        this._rootContainer = null;
        this._namedWeavePoints = new Map();
        // Loose ends are:
        //  - Choices or Gathers that need to be joined up
        //  - Explicit Divert to gather points (i.e. "->" without a target)
        this.looseEnds = [];
        this.gatherPointsToResolve = [];
        this.ResolveWeavePointNaming = () => {
            var _a, _b, _c;
            const namedWeavePoints = [
                ...this.FindAll(Gather_1.Gather)((w) => !(w.name === null || w.name === undefined)),
                ...this.FindAll(Choice_1.Choice)((w) => !(w.name === null || w.name === undefined)),
            ];
            this._namedWeavePoints = new Map();
            for (const weavePoint of namedWeavePoints) {
                // Check for weave point naming collisions
                const existingWeavePoint = this.namedWeavePoints.get(((_a = weavePoint.identifier) === null || _a === void 0 ? void 0 : _a.name) || "");
                if (existingWeavePoint) {
                    const typeName = existingWeavePoint instanceof Gather_1.Gather ? "gather" : "choice";
                    const existingObj = existingWeavePoint;
                    this.Error(`A ${typeName} with the same label name '${weavePoint.name}' already exists in this context on line ${existingObj.debugMetadata
                        ? existingObj.debugMetadata.startLineNumber
                        : "NO DEBUG METADATA AVAILABLE"}`, weavePoint);
                }
                if ((_b = weavePoint.identifier) === null || _b === void 0 ? void 0 : _b.name) {
                    this.namedWeavePoints.set((_c = weavePoint.identifier) === null || _c === void 0 ? void 0 : _c.name, weavePoint);
                }
            }
        };
        this.ConstructWeaveHierarchyFromIndentation = () => {
            // Find nested indentation and convert to a proper object hierarchy
            // (i.e. indented content is replaced with a Weave object that contains
            // that nested content)
            let contentIdx = 0;
            while (contentIdx < this.content.length) {
                const obj = this.content[contentIdx];
                // Choice or Gather
                if (obj instanceof Choice_1.Choice || obj instanceof Gather_1.Gather) {
                    const weavePoint = obj;
                    const weaveIndentIdx = weavePoint.indentationDepth - 1;
                    // Inner level indentation - recurse
                    if (weaveIndentIdx > this.baseIndentIndex) {
                        // Step through content until indent jumps out again
                        let innerWeaveStartIdx = contentIdx;
                        while (contentIdx < this.content.length) {
                            const innerWeaveObj = (0, TypeAssertion_1.asOrNull)(this.content[contentIdx], Choice_1.Choice) ||
                                (0, TypeAssertion_1.asOrNull)(this.content[contentIdx], Gather_1.Gather);
                            if (innerWeaveObj !== null) {
                                const innerIndentIdx = innerWeaveObj.indentationDepth - 1;
                                if (innerIndentIdx <= this.baseIndentIndex) {
                                    break;
                                }
                            }
                            contentIdx += 1;
                        }
                        const weaveContentCount = contentIdx - innerWeaveStartIdx;
                        const weaveContent = this.content.slice(innerWeaveStartIdx, innerWeaveStartIdx + weaveContentCount);
                        this.content.splice(innerWeaveStartIdx, weaveContentCount);
                        const weave = new Weave(weaveContent, weaveIndentIdx);
                        this.InsertContent(innerWeaveStartIdx, weave);
                        // Continue iteration from this point
                        contentIdx = innerWeaveStartIdx;
                    }
                }
                contentIdx += 1;
            }
        };
        // When the indentation wasn't told to us at construction time using
        // a choice point with a known indentation level, we may be told to
        // determine the indentation level by incrementing from our closest ancestor.
        this.DetermineBaseIndentationFromContent = (contentList) => {
            for (const obj of contentList) {
                if (obj instanceof Choice_1.Choice || obj instanceof Gather_1.Gather) {
                    return obj.indentationDepth - 1;
                }
            }
            // No weave points, so it doesn't matter
            return 0;
        };
        this.GenerateRuntimeObject = () => {
            this._rootContainer = new Container_1.Container();
            this.currentContainer = this._rootContainer;
            this.looseEnds = [];
            this.gatherPointsToResolve = [];
            // Iterate through content for the block at this level of indentation
            //  - Normal content is nested under Choices and Gathers
            //  - Blocks that are further indented cause recursion
            //  - Keep track of loose ends so that they can be diverted to Gathers
            for (const obj of this.content) {
                // Choice or Gather
                if (obj instanceof Choice_1.Choice || obj instanceof Gather_1.Gather) {
                    this.AddRuntimeForWeavePoint(obj);
                }
                else {
                    // Non-weave point
                    if (obj instanceof Weave) {
                        // Nested weave
                        const weave = obj;
                        this.AddRuntimeForNestedWeave(weave);
                        this.gatherPointsToResolve.splice(0, 0, ...weave.gatherPointsToResolve);
                    }
                    else {
                        // Other object
                        // May be complex object that contains statements - e.g. a multi-line conditional
                        this.AddGeneralRuntimeContent(obj.runtimeObject);
                    }
                }
            }
            // Pass any loose ends up the hierarhcy
            this.PassLooseEndsToAncestors();
            return this._rootContainer;
        };
        // Found gather point:
        //  - gather any loose ends
        //  - set the gather as the main container to dump new content in
        this.AddRuntimeForGather = (gather) => {
            // Determine whether this Gather should be auto-entered:
            //  - It is auto-entered if there were no choices in the last section
            //  - A section is "since the previous gather" - so reset now
            const autoEnter = !this.hasSeenChoiceInSection;
            this.hasSeenChoiceInSection = false;
            const gatherContainer = gather.runtimeContainer;
            if (!gather.name) {
                // Use disallowed character so it's impossible to have a name collision
                gatherContainer.name = `g-${this._unnamedGatherCount}`;
                this._unnamedGatherCount += 1;
            }
            if (autoEnter) {
                if (!this.currentContainer) {
                    throw new Error();
                }
                // Auto-enter: include in main content
                this.currentContainer.AddContent(gatherContainer);
            }
            else {
                // Don't auto-enter:
                // Add this gather to the main content, but only accessible
                // by name so that it isn't stepped into automatically, but only via
                // a divert from a loose end.
                this.rootContainer.AddToNamedContentOnly(gatherContainer);
            }
            // Consume loose ends: divert them to this gather
            for (const looseEndWeavePoint of this.looseEnds) {
                const looseEnd = looseEndWeavePoint;
                // Skip gather loose ends that are at the same level
                // since they'll be handled by the auto-enter code below
                // that only jumps into the gather if (current runtime choices == 0)
                if (looseEnd instanceof Gather_1.Gather) {
                    const prevGather = looseEnd;
                    if (prevGather.indentationDepth == gather.indentationDepth) {
                        continue;
                    }
                }
                let divert = null;
                if (looseEnd instanceof Divert_1.Divert) {
                    divert = looseEnd.runtimeObject;
                }
                else {
                    divert = new Divert_2.Divert();
                    const looseWeavePoint = looseEnd;
                    if (!looseWeavePoint.runtimeContainer) {
                        throw new Error();
                    }
                    looseWeavePoint.runtimeContainer.AddContent(divert);
                }
                // Pass back knowledge of this loose end being diverted
                // to the FlowBase so that it can maintain a list of them,
                // and resolve the divert references later
                this.gatherPointsToResolve.push(new GatherPointToResolve_1.GatherPointToResolve(divert, gatherContainer));
            }
            this.looseEnds = [];
            // Replace the current container itself
            this.currentContainer = gatherContainer;
        };
        this.AddRuntimeForWeavePoint = (weavePoint) => {
            // Current level Gather
            if (weavePoint instanceof Gather_1.Gather) {
                this.AddRuntimeForGather(weavePoint);
            }
            // Current level choice
            else if (weavePoint instanceof Choice_1.Choice) {
                if (!this.currentContainer) {
                    throw new Error();
                }
                // Gathers that contain choices are no longer loose ends
                // (same as when weave points get nested content)
                if (this.previousWeavePoint instanceof Gather_1.Gather) {
                    this.looseEnds.splice(this.looseEnds.indexOf(this.previousWeavePoint), 1);
                }
                // Add choice point content
                const choice = weavePoint; //, Choice);
                this.currentContainer.AddContent(choice.runtimeObject);
                if (!choice.innerContentContainer) {
                    throw new Error();
                } //guaranteed not to happen
                // Add choice's inner content to self
                choice.innerContentContainer.name = `c-${this._choiceCount}`;
                this.currentContainer.AddToNamedContentOnly(choice.innerContentContainer);
                this._choiceCount += 1;
                this.hasSeenChoiceInSection = true;
            }
            // Keep track of loose ends
            this.addContentToPreviousWeavePoint = false; // default
            if (this.WeavePointHasLooseEnd(weavePoint)) {
                this.looseEnds.push(weavePoint);
                const looseChoice = (0, TypeAssertion_1.asOrNull)(weavePoint, Choice_1.Choice);
                if (looseChoice) {
                    this.addContentToPreviousWeavePoint = true;
                }
            }
            this.previousWeavePoint = weavePoint;
        };
        // Add nested block at a greater indentation level
        this.AddRuntimeForNestedWeave = (nestedResult) => {
            // Add this inner block to current container
            // (i.e. within the main container, or within the last defined Choice/Gather)
            this.AddGeneralRuntimeContent(nestedResult.rootContainer);
            // Now there's a deeper indentation level, the previous weave point doesn't
            // count as a loose end (since it will have content to go to)
            if (this.previousWeavePoint !== null) {
                this.looseEnds.splice(this.looseEnds.indexOf(this.previousWeavePoint), 1);
                this.addContentToPreviousWeavePoint = false;
            }
        };
        // Normal content gets added into the latest Choice or Gather by default,
        // unless there hasn't been one yet.
        this.AddGeneralRuntimeContent = (content) => {
            // Content is allowed to evaluate runtimeObject to null
            // (e.g. AuthorWarning, which doesn't make it into the runtime)
            if (content === null) {
                return;
            }
            if (this.addContentToPreviousWeavePoint) {
                if (!this.previousWeavePoint ||
                    !this.previousWeavePoint.runtimeContainer) {
                    throw new Error();
                }
                this.previousWeavePoint.runtimeContainer.AddContent(content);
            }
            else {
                if (!this.currentContainer) {
                    throw new Error();
                }
                this.currentContainer.AddContent(content);
            }
        };
        this.PassLooseEndsToAncestors = () => {
            if (this.looseEnds.length === 0) {
                return;
            }
            // Search for Weave ancestor to pass loose ends to for gathering.
            // There are two types depending on whether the current weave
            // is separated by a conditional or sequence.
            //  - An "inner" weave is one that is directly connected to the current
            //    weave - i.e. you don't have to pass through a conditional or
            //    sequence to get to it. We're allowed to pass all loose ends to
            //    one of these.
            //  - An "outer" weave is one that is outside of a conditional/sequence
            //    that the current weave is nested within. We're only allowed to
            //    pass gathers (i.e. 'normal flow') loose ends up there, not normal
            //    choices. The rule is that choices have to be diverted explicitly
            //    by the author since it's ambiguous where flow should go otherwise.
            //
            // e.g.:
            //
            //   - top                       <- e.g. outer weave
            //   {true:
            //       * choice                <- e.g. inner weave
            //         * * choice 2
            //             more content      <- e.g. current weave
            //       * choice 2
            //   }
            //   - more of outer weave
            //
            let closestInnerWeaveAncestor = null;
            let closestOuterWeaveAncestor = null;
            // Find inner and outer ancestor weaves as defined above.
            let nested = false;
            for (let ancestor = this.parent; ancestor !== null; ancestor = ancestor.parent) {
                // Found ancestor?
                const weaveAncestor = (0, TypeAssertion_1.asOrNull)(ancestor, Weave);
                if (weaveAncestor) {
                    if (!nested && closestInnerWeaveAncestor === null) {
                        closestInnerWeaveAncestor = weaveAncestor;
                    }
                    if (nested && closestOuterWeaveAncestor === null) {
                        closestOuterWeaveAncestor = weaveAncestor;
                    }
                }
                // Weaves nested within Sequences or Conditionals are
                // "sealed" - any loose ends require explicit diverts.
                if (ancestor instanceof Sequence_1.Sequence || ancestor instanceof Conditional_1.Conditional) {
                    nested = true;
                }
            }
            // No weave to pass loose ends to at all?
            if (closestInnerWeaveAncestor === null &&
                closestOuterWeaveAncestor === null) {
                return;
            }
            // Follow loose end passing logic as defined above
            for (let ii = this.looseEnds.length - 1; ii >= 0; ii -= 1) {
                const looseEnd = this.looseEnds[ii];
                let received = false;
                if (nested) {
                    // This weave is nested within a conditional or sequence:
                    //  - choices can only be passed up to direct ancestor ("inner") weaves
                    //  - gathers can be passed up to either, but favour the closer (inner) weave
                    //    if there is one
                    if (looseEnd instanceof Choice_1.Choice && closestInnerWeaveAncestor !== null) {
                        closestInnerWeaveAncestor.ReceiveLooseEnd(looseEnd);
                        received = true;
                    }
                    else if (!(looseEnd instanceof Choice_1.Choice)) {
                        const receivingWeave = closestInnerWeaveAncestor || closestOuterWeaveAncestor;
                        if (receivingWeave !== null) {
                            receivingWeave.ReceiveLooseEnd(looseEnd);
                            received = true;
                        }
                    }
                }
                else {
                    // No nesting, all loose ends can be safely passed up
                    if (closestInnerWeaveAncestor === null || closestInnerWeaveAncestor === void 0 ? void 0 : closestInnerWeaveAncestor.hasOwnProperty("ReceiveLooseEnd")) {
                        closestInnerWeaveAncestor.ReceiveLooseEnd(looseEnd);
                    }
                    received = true;
                }
                if (received) {
                    this.looseEnds.splice(ii, 1);
                }
            }
        };
        this.ReceiveLooseEnd = (childWeaveLooseEnd) => {
            this.looseEnds.push(childWeaveLooseEnd);
        };
        this.WeavePointNamed = (name) => {
            if (!this.namedWeavePoints) {
                return null;
            }
            let weavePointResult = this.namedWeavePoints.get(name);
            if (weavePointResult) {
                return weavePointResult;
            }
            return null;
        };
        // Global VARs and CONSTs are treated as "outside of the flow"
        // when iterating over content that follows loose ends
        this.IsGlobalDeclaration = (obj) => {
            const varAss = (0, TypeAssertion_1.asOrNull)(obj, VariableAssignment_1.VariableAssignment);
            if (varAss && varAss.isGlobalDeclaration && varAss.isDeclaration) {
                return true;
            }
            const constDecl = (0, TypeAssertion_1.asOrNull)(obj, ConstantDeclaration_1.ConstantDeclaration);
            if (constDecl) {
                return true;
            }
            return false;
        };
        // While analysing final loose ends, we look to see whether there
        // are any diverts etc which choices etc divert from
        this.ContentThatFollowsWeavePoint = (weavePoint) => {
            const returned = [];
            const obj = weavePoint;
            // Inner content first (e.g. for a choice)
            if (obj.content !== null) {
                for (const contentObj of obj.content) {
                    // Global VARs and CONSTs are treated as "outside of the flow"
                    if (this.IsGlobalDeclaration(contentObj)) {
                        continue;
                    }
                    returned.push(contentObj);
                }
            }
            const parentWeave = (0, TypeAssertion_1.asOrNull)(obj.parent, Weave);
            if (parentWeave === null) {
                throw new Error("Expected weave point parent to be weave?");
            }
            const weavePointIdx = parentWeave.content.indexOf(obj);
            for (let ii = weavePointIdx + 1; ii < parentWeave.content.length; ii += 1) {
                const laterObj = parentWeave.content[ii];
                // Global VARs and CONSTs are treated as "outside of the flow"
                if (this.IsGlobalDeclaration(laterObj)) {
                    continue;
                }
                // End of the current flow
                // if (laterObj instanceof IWeavePoint) // cannot test on interface in ts
                if (laterObj instanceof Choice_1.Choice || laterObj instanceof Gather_1.Gather) {
                    break;
                }
                // Other weaves will be have their own loose ends
                if (laterObj instanceof Weave) {
                    break;
                }
                returned.push(laterObj);
            }
            return returned;
        };
        this.ValidateTermination = (badTerminationHandler) => {
            // Don't worry if the last object in the flow is a "TODO",
            // even if there are other loose ends in other places
            if (this.lastParsedSignificantObject instanceof AuthorWarning_1.AuthorWarning) {
                return;
            }
            // By now, any sub-weaves will have passed loose ends up to the root weave (this).
            // So there are 2 possible situations:
            //  - There are loose ends from somewhere in the flow.
            //    These aren't necessarily "real" loose ends - they're weave points
            //    that don't connect to any lower weave points, so we just
            //    have to check that they terminate properly.
            //  - This weave is just a list of content with no actual weave points,
            //    so we just need to check that the list of content terminates.
            const hasLooseEnds = this.looseEnds !== null && this.looseEnds.length > 0;
            if (hasLooseEnds) {
                for (const looseEnd of this.looseEnds) {
                    const looseEndFlow = this.ContentThatFollowsWeavePoint(looseEnd);
                    this.ValidateFlowOfObjectsTerminates(looseEndFlow, looseEnd, badTerminationHandler);
                }
            }
            else {
                // No loose ends... is there any inner weaving at all?
                // If not, make sure the single content stream is terminated correctly
                //
                // If there's any actual weaving, assume that content is
                // terminated correctly since we would've had a loose end otherwise
                for (const obj of this.content) {
                    if (obj instanceof Choice_1.Choice || obj instanceof Divert_1.Divert) {
                        return;
                    }
                }
                // Straight linear flow? Check it terminates
                this.ValidateFlowOfObjectsTerminates(this.content, this, badTerminationHandler);
            }
        };
        this.BadNestedTerminationHandler = (terminatingObj) => {
            let conditional = null;
            for (let ancestor = terminatingObj.parent; ancestor !== null; ancestor = ancestor.parent) {
                if (ancestor instanceof Sequence_1.Sequence || ancestor instanceof Conditional_1.Conditional) {
                    conditional = (0, TypeAssertion_1.asOrNull)(ancestor, Conditional_1.Conditional);
                    break;
                }
            }
            let errorMsg = "Choices nested in conditionals or sequences need to explicitly divert afterwards.";
            // Tutorialise proper choice syntax if this looks like a single choice within a condition, e.g.
            // { condition:
            //      * choice
            // }
            if (conditional !== null) {
                let numChoices = conditional.FindAll(Choice_1.Choice)().length;
                if (numChoices === 1) {
                    errorMsg = `Choices with conditions should be written: '* {condition} choice'. Otherwise, ${errorMsg.toLowerCase()}`;
                }
            }
            this.Error(errorMsg, terminatingObj);
        };
        this.ValidateFlowOfObjectsTerminates = (objFlow, defaultObj, badTerminationHandler) => {
            let terminated = false;
            let terminatingObj = defaultObj;
            for (const flowObj of objFlow) {
                const divert = flowObj.Find(Divert_1.Divert)((d) => !d.isThread &&
                    !d.isTunnel &&
                    !d.isFunctionCall &&
                    !(d.parent instanceof DivertTarget_1.DivertTarget));
                if (divert !== null) {
                    terminated = true;
                }
                if (flowObj.Find(TunnelOnwards_1.TunnelOnwards)() != null) {
                    terminated = true;
                    break;
                }
                terminatingObj = flowObj;
            }
            if (!terminated) {
                // Author has left a note to self here - clearly we don't need
                // to leave them with another warning since they know what they're doing.
                if (terminatingObj instanceof AuthorWarning_1.AuthorWarning) {
                    return;
                }
                badTerminationHandler(terminatingObj);
            }
        };
        this.WeavePointHasLooseEnd = (weavePoint) => {
            // No content, must be a loose end.
            if (weavePoint.content === null) {
                return true;
            }
            // If a weave point is diverted from, it doesn't have a loose end.
            // Detect a divert object within a weavePoint's main content
            // Work backwards since we're really interested in the end,
            // although it doesn't actually make a difference!
            // (content after a divert will simply be inaccessible)
            for (let ii = weavePoint.content.length - 1; ii >= 0; --ii) {
                let innerDivert = (0, TypeAssertion_1.asOrNull)(weavePoint.content[ii], Divert_1.Divert);
                if (innerDivert) {
                    const willReturn = innerDivert.isThread ||
                        innerDivert.isTunnel ||
                        innerDivert.isFunctionCall;
                    if (!willReturn) {
                        return false;
                    }
                }
            }
            return true;
        };
        // Enforce rule that weave points must not have the same
        // name as any stitches or knots upwards in the hierarchy
        this.CheckForWeavePointNamingCollisions = () => {
            if (!this.namedWeavePoints) {
                return;
            }
            const ancestorFlows = [];
            for (const obj of this.ancestry) {
                const flow = (0, TypeAssertion_1.asOrNull)(obj, FlowBase_1.FlowBase);
                if (flow) {
                    ancestorFlows.push(flow);
                }
                else {
                    break;
                }
            }
            for (const [weavePointName, weavePoint] of this.namedWeavePoints) {
                for (const flow of ancestorFlows) {
                    // Shallow search
                    const otherContentWithName = flow.ContentWithNameAtLevel(weavePointName);
                    if (otherContentWithName && otherContentWithName !== weavePoint) {
                        const errorMsg = `${weavePoint.GetType()} '${weavePointName}' has the same label name as a ${otherContentWithName.GetType()} (on ${otherContentWithName.debugMetadata})`;
                        this.Error(errorMsg, weavePoint);
                    }
                }
            }
        };
        if (indentIndex == -1) {
            this.baseIndentIndex = this.DetermineBaseIndentationFromContent(cont);
        }
        else {
            this.baseIndentIndex = indentIndex;
        }
        this.AddContent(cont);
        this.ConstructWeaveHierarchyFromIndentation();
    }
    get typeName() {
        return "Weave";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        // Check that choices nested within conditionals and sequences are terminated
        if (this.looseEnds !== null && this.looseEnds.length > 0) {
            let isNestedWeave = false;
            for (let ancestor = this.parent; ancestor !== null; ancestor = ancestor.parent) {
                if (ancestor instanceof Sequence_1.Sequence || ancestor instanceof Conditional_1.Conditional) {
                    isNestedWeave = true;
                    break;
                }
            }
            if (isNestedWeave) {
                this.ValidateTermination(this.BadNestedTerminationHandler);
            }
        }
        for (const gatherPoint of this.gatherPointsToResolve) {
            gatherPoint.divert.targetPath = gatherPoint.targetRuntimeObj.path;
        }
        this.CheckForWeavePointNamingCollisions();
    }
}
exports.Weave = Weave;
//# sourceMappingURL=Weave.js.map