"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.IncDecExpression = void 0;
const ContentList_1 = require("../ContentList");
const Expression_1 = require("./Expression");
const FlowBase_1 = require("../Flow/FlowBase");
const NativeFunctionCall_1 = require("../../../../engine/NativeFunctionCall");
const Value_1 = require("../../../../engine/Value");
const VariableAssignment_1 = require("../../../../engine/VariableAssignment");
const VariableReference_1 = require("../../../../engine/VariableReference");
const Weave_1 = require("../Weave");
class IncDecExpression extends Expression_1.Expression {
    constructor(varIdentifier, isIncOrExpression, isInc) {
        super();
        this.varIdentifier = varIdentifier;
        this._runtimeAssignment = null;
        this.expression = null;
        this.GenerateIntoContainer = (container) => {
            // x = x + y
            // ^^^ ^ ^ ^
            //  4  1 3 2
            // Reverse polish notation: (x 1 +) (assign to x)
            var _a, _b;
            // 1.
            container.AddContent(new VariableReference_1.VariableReference(((_a = this.varIdentifier) === null || _a === void 0 ? void 0 : _a.name) || null));
            // 2.
            // - Expression used in the form ~ x += y
            // - Simple version: ~ x++
            if (this.expression) {
                this.expression.GenerateIntoContainer(container);
            }
            else {
                container.AddContent(new Value_1.IntValue(1));
            }
            // 3.
            container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName(this.isInc ? "+" : "-"));
            // 4.
            this._runtimeAssignment = new VariableAssignment_1.VariableAssignment(((_b = this.varIdentifier) === null || _b === void 0 ? void 0 : _b.name) || null, false);
            container.AddContent(this._runtimeAssignment);
        };
        this.toString = () => {
            var _a, _b;
            if (this.expression) {
                return `${(_a = this.varIdentifier) === null || _a === void 0 ? void 0 : _a.name}${this.isInc ? " += " : " -= "}${this.expression}`;
            }
            return `${(_b = this.varIdentifier) === null || _b === void 0 ? void 0 : _b.name}` + (this.isInc ? "++" : "--");
        };
        if (isIncOrExpression instanceof Expression_1.Expression) {
            this.expression = isIncOrExpression;
            this.AddContent(this.expression);
            this.isInc = Boolean(isInc);
        }
        else {
            this.isInc = isIncOrExpression;
        }
    }
    get typeName() {
        return "IncDecExpression";
    }
    ResolveReferences(context) {
        var _a;
        super.ResolveReferences(context);
        const varResolveResult = context.ResolveVariableWithName(((_a = this.varIdentifier) === null || _a === void 0 ? void 0 : _a.name) || "", this);
        if (!varResolveResult.found) {
            this.Error(`variable for ${this.incrementDecrementWord} could not be found: '${this.varIdentifier}' after searching: {this.descriptionOfScope}`);
        }
        if (!this._runtimeAssignment) {
            throw new Error();
        }
        this._runtimeAssignment.isGlobal = varResolveResult.isGlobal;
        if (!(this.parent instanceof Weave_1.Weave) &&
            !(this.parent instanceof FlowBase_1.FlowBase) &&
            !(this.parent instanceof ContentList_1.ContentList)) {
            this.Error(`Can't use ${this.incrementDecrementWord} as sub-expression`);
        }
    }
    get incrementDecrementWord() {
        if (this.isInc) {
            return "increment";
        }
        return "decrement";
    }
}
exports.IncDecExpression = IncDecExpression;
//# sourceMappingURL=IncDecExpression.js.map