"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.DivertTarget = void 0;
const BinaryExpression_1 = require("../Expression/BinaryExpression");
const Choice_1 = require("../Choice");
const Conditional_1 = require("../Conditional/Conditional");
const ConditionalSingleBranch_1 = require("../Conditional/ConditionalSingleBranch");
const Value_1 = require("../../../../engine/Value");
const Expression_1 = require("../Expression/Expression");
const FlowBase_1 = require("../Flow/FlowBase");
const FunctionCall_1 = require("../FunctionCall");
const MultipleConditionExpression_1 = require("../Expression/MultipleConditionExpression");
const VariableReference_1 = require("../Variable/VariableReference");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
class DivertTarget extends Expression_1.Expression {
    get runtimeDivert() {
        if (!this._runtimeDivert) {
            throw new Error();
        }
        return this._runtimeDivert;
    }
    get runtimeDivertTargetValue() {
        if (!this._runtimeDivertTargetValue) {
            throw new Error();
        }
        return this._runtimeDivertTargetValue;
    }
    constructor(divert) {
        super();
        this._runtimeDivert = null;
        this._runtimeDivertTargetValue = null;
        this.GenerateIntoContainer = (container) => {
            this.divert.GenerateRuntimeObject();
            this._runtimeDivert = this.divert.runtimeDivert;
            this._runtimeDivertTargetValue = new Value_1.DivertTargetValue();
            container.AddContent(this.runtimeDivertTargetValue);
        };
        // Equals override necessary in order to check for CONST multiple definition equality
        this.Equals = (obj) => {
            const otherDivTarget = (0, TypeAssertion_1.asOrNull)(obj, DivertTarget);
            if (!otherDivTarget ||
                !this.divert.target ||
                !otherDivTarget.divert.target) {
                return false;
            }
            const targetStr = this.divert.target.dotSeparatedComponents;
            const otherTargetStr = otherDivTarget.divert.target.dotSeparatedComponents;
            return targetStr === otherTargetStr;
        };
        this.divert = this.AddContent(divert);
    }
    get typeName() {
        return "DivertTarget";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        if (this.divert.isDone || this.divert.isEnd) {
            this.Error(`Can't use -> DONE or -> END as variable divert targets`, this);
            return;
        }
        let usageContext = this;
        while (usageContext && usageContext instanceof Expression_1.Expression) {
            let badUsage = false;
            let foundUsage = false;
            const usageParent = usageContext.parent;
            if (usageParent instanceof BinaryExpression_1.BinaryExpression) {
                // Only allowed to compare for equality
                const binaryExprParent = usageParent;
                if (binaryExprParent.opName !== "==" &&
                    binaryExprParent.opName !== "!=") {
                    badUsage = true;
                }
                else {
                    if (!(binaryExprParent.leftExpression instanceof DivertTarget ||
                        binaryExprParent.leftExpression instanceof VariableReference_1.VariableReference)) {
                        badUsage = true;
                    }
                    else if (!(binaryExprParent.rightExpression instanceof DivertTarget ||
                        binaryExprParent.rightExpression instanceof VariableReference_1.VariableReference)) {
                        badUsage = true;
                    }
                }
                foundUsage = true;
            }
            else if (usageParent instanceof FunctionCall_1.FunctionCall) {
                const funcCall = usageParent;
                if (!funcCall.isTurnsSince && !funcCall.isReadCount) {
                    badUsage = true;
                }
                foundUsage = true;
            }
            else if (usageParent instanceof Expression_1.Expression) {
                badUsage = true;
                foundUsage = true;
            }
            else if (usageParent instanceof MultipleConditionExpression_1.MultipleConditionExpression) {
                badUsage = true;
                foundUsage = true;
            }
            else if (usageParent instanceof Choice_1.Choice &&
                usageParent.condition === usageContext) {
                badUsage = true;
                foundUsage = true;
            }
            else if (usageParent instanceof Conditional_1.Conditional ||
                usageParent instanceof ConditionalSingleBranch_1.ConditionalSingleBranch) {
                badUsage = true;
                foundUsage = true;
            }
            if (badUsage) {
                this.Error(`Can't use a divert target like that. Did you intend to call '${this.divert.target}' as a function: likeThis(), or check the read count: likeThis, with no arrows?`, this);
            }
            if (foundUsage) {
                break;
            }
            usageContext = usageParent;
        }
        // Example ink for this case:
        //
        //     VAR x = -> blah
        //
        // ...which means that "blah" is expected to be a literal stitch target rather
        // than a variable name. We can't really intelligently recover from this (e.g. if blah happens to
        // contain a divert target itself) since really we should be generating a variable reference
        // rather than a concrete DivertTarget, so we list it as an error.
        if (this.runtimeDivert.hasVariableTarget) {
            if (!this.divert.target) {
                throw new Error();
            }
            this.Error(`Since '${this.divert.target.dotSeparatedComponents}' is a variable, it shouldn't be preceded by '->' here.`);
        }
        // Main resolve
        this.runtimeDivert.targetPath &&
            (this.runtimeDivertTargetValue.targetPath =
                this.runtimeDivert.targetPath);
        // Tell hard coded (yet variable) divert targets that they also need to be counted
        // TODO: Only detect DivertTargets that are values rather than being used directly for
        // read or turn counts. Should be able to detect this by looking for other uses of containerForCounting
        let targetContent = this.divert.targetContent;
        if (targetContent !== null) {
            let target = targetContent.containerForCounting;
            if (target !== null) {
                // Purpose is known: used directly in TURNS_SINCE(-> divTarg)
                const parentFunc = (0, TypeAssertion_1.asOrNull)(this.parent, FunctionCall_1.FunctionCall);
                if (parentFunc && parentFunc.isTurnsSince) {
                    target.turnIndexShouldBeCounted = true;
                }
                else {
                    // Unknown purpose, count everything
                    target.visitsShouldBeCounted = true;
                    target.turnIndexShouldBeCounted = true;
                }
            }
            // Unfortunately not possible:
            // https://github.com/inkle/ink/issues/538
            //
            // VAR func = -> double
            //
            // === function double(ref x)
            //    ~ x = x * 2
            //
            // Because when generating the parameters for a function
            // to be called, it needs to know ahead of time when
            // compiling whether to pass a variable reference or value.
            //
            let targetFlow = (0, TypeAssertion_1.asOrNull)(targetContent, FlowBase_1.FlowBase);
            if (targetFlow != null && targetFlow.args !== null) {
                for (const arg of targetFlow.args) {
                    if (arg.isByReference) {
                        this.Error(`Can't store a divert target to a knot or function that has by-reference arguments ('${targetFlow.identifier}' has 'ref ${arg.identifier}').`);
                    }
                }
            }
        }
    }
}
exports.DivertTarget = DivertTarget;
//# sourceMappingURL=DivertTarget.js.map