"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ConditionalSingleBranch = void 0;
const Container_1 = require("../../../../engine/Container");
const ControlCommand_1 = require("../../../../engine/ControlCommand");
const Divert_1 = require("../../../../engine/Divert");
const Object_1 = require("../Object");
const NativeFunctionCall_1 = require("../../../../engine/NativeFunctionCall");
const Value_1 = require("../../../../engine/Value");
const Text_1 = require("../Text");
const Weave_1 = require("../Weave");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
class ConditionalSingleBranch extends Object_1.ParsedObject {
    // When each branch has its own expression like a switch statement,
    // this is non-null. e.g.
    // { x:
    //    - 4: the value of x is four (ownExpression is the value 4)
    //    - 3: the value of x is three
    // }
    get ownExpression() {
        return this._ownExpression;
    }
    set ownExpression(value) {
        this._ownExpression = value;
        if (this._ownExpression) {
            this.AddContent(this._ownExpression);
        }
    }
    constructor(content) {
        super();
        this._contentContainer = null;
        this._conditionalDivert = null;
        this._ownExpression = null;
        this._innerWeave = null;
        // bool condition, e.g.:
        // { 5 == 4:
        //   - the true branch
        //   - the false branch
        // }
        this.isTrueBranch = false;
        // In the above example, match equality of x with 4 for the first branch.
        // This is as opposed to simply evaluating boolean equality for each branch,
        // example when shouldMatchEquality is FALSE:
        // {
        //    3 > 2:  This will happen
        //    2 > 3:  This won't happen
        // }
        this.matchingEquality = false;
        this.isElse = false;
        this.isInline = false;
        this.returnDivert = null;
        // Runtime content can be summarised as follows:
        //  - Evaluate an expression if necessary to branch on
        //  - Branch to a named container if true
        //       - Divert back to main flow
        //         (owner Conditional is in control of this target point)
        this.GenerateRuntimeObject = () => {
            // Check for common mistake, of putting "else:" instead of "- else:"
            if (this._innerWeave) {
                for (const c of this._innerWeave.content) {
                    const text = (0, TypeAssertion_1.asOrNull)(c, Text_1.Text);
                    if (text) {
                        // Don't need to trim at the start since the parser handles that already
                        if (text.text.startsWith("else:")) {
                            this.Warning("Saw the text 'else:' which is being treated as content. Did you mean '- else:'?", text);
                        }
                    }
                }
            }
            const container = new Container_1.Container();
            // Are we testing against a condition that's used for more than just this
            // branch? If so, the first thing we need to do is replicate the value that's
            // on the evaluation stack so that we don't fully consume it, in case other
            // branches need to use it.
            const duplicatesStackValue = this.matchingEquality && !this.isElse;
            if (duplicatesStackValue) {
                container.AddContent(ControlCommand_1.ControlCommand.Duplicate());
            }
            this._conditionalDivert = new Divert_1.Divert();
            // else clause is unconditional catch-all, otherwise the divert is conditional
            this._conditionalDivert.isConditional = !this.isElse;
            // Need extra evaluation?
            if (!this.isTrueBranch && !this.isElse) {
                const needsEval = this.ownExpression !== null;
                if (needsEval) {
                    container.AddContent(ControlCommand_1.ControlCommand.EvalStart());
                }
                if (this.ownExpression) {
                    this.ownExpression.GenerateIntoContainer(container);
                }
                // Uses existing duplicated value
                if (this.matchingEquality) {
                    container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName("=="));
                }
                if (needsEval) {
                    container.AddContent(ControlCommand_1.ControlCommand.EvalEnd());
                }
            }
            // Will pop from stack if conditional
            container.AddContent(this._conditionalDivert);
            this._contentContainer = this.GenerateRuntimeForContent();
            this._contentContainer.name = "b";
            // Multi-line conditionals get a newline at the start of each branch
            // (as opposed to the start of the multi-line conditional since the condition
            //  may evaluate to false.)
            if (!this.isInline) {
                this._contentContainer.InsertContent(new Value_1.StringValue("\n"), 0);
            }
            if (duplicatesStackValue || (this.isElse && this.matchingEquality)) {
                this._contentContainer.InsertContent(ControlCommand_1.ControlCommand.PopEvaluatedValue(), 0);
            }
            container.AddToNamedContentOnly(this._contentContainer);
            this.returnDivert = new Divert_1.Divert();
            this._contentContainer.AddContent(this.returnDivert);
            return container;
        };
        this.GenerateRuntimeForContent = () => {
            // Empty branch - create empty container
            if (this._innerWeave === null) {
                return new Container_1.Container();
            }
            return this._innerWeave.rootContainer;
        };
        // Branches are allowed to be empty
        if (content) {
            this._innerWeave = new Weave_1.Weave(content);
            this.AddContent(this._innerWeave);
        }
    }
    get typeName() {
        return "ConditionalSingleBranch";
    }
    ResolveReferences(context) {
        if (!this._conditionalDivert || !this._contentContainer) {
            throw new Error();
        }
        this._conditionalDivert.targetPath = this._contentContainer.path;
        super.ResolveReferences(context);
    }
}
exports.ConditionalSingleBranch = ConditionalSingleBranch;
//# sourceMappingURL=ConditionalSingleBranch.js.map