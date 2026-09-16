"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.UnaryExpression = void 0;
const Expression_1 = require("./Expression");
const NativeFunctionCall_1 = require("../../../../engine/NativeFunctionCall");
const NumberExpression_1 = require("./NumberExpression");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
class UnaryExpression extends Expression_1.Expression {
    get nativeNameForOp() {
        // Replace "-" with "_" to make it unique (compared to subtraction)
        if (this.op === "-") {
            return "_";
        }
        else if (this.op === "not") {
            return "!";
        }
        return this.op;
    }
    constructor(inner, op) {
        super();
        this.op = op;
        this.GenerateIntoContainer = (container) => {
            this.innerExpression.GenerateIntoContainer(container);
            container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName(this.nativeNameForOp));
        };
        this.toString = () => this.nativeNameForOp + this.innerExpression;
        this.innerExpression = this.AddContent(inner);
    }
    get typeName() {
        return "UnaryExpression";
    }
}
exports.UnaryExpression = UnaryExpression;
// Attempt to flatten inner expression immediately
// e.g. convert (-(5)) into (-5)
UnaryExpression.WithInner = (inner, op) => {
    const innerNumber = (0, TypeAssertion_1.asOrNull)(inner, NumberExpression_1.NumberExpression);
    if (innerNumber) {
        if (op === "-") {
            if (innerNumber.isInt()) {
                return new NumberExpression_1.NumberExpression(-innerNumber.value, "int");
            }
            else if (innerNumber.isFloat()) {
                return new NumberExpression_1.NumberExpression(-innerNumber.value, "float");
            }
        }
        else if (op == "!" || op == "not") {
            if (innerNumber.isInt()) {
                return new NumberExpression_1.NumberExpression(innerNumber.value == 0, "bool");
            }
            else if (innerNumber.isFloat()) {
                return new NumberExpression_1.NumberExpression(innerNumber.value == 0.0, "bool");
            }
            else if (innerNumber.isBool()) {
                return new NumberExpression_1.NumberExpression(!innerNumber.value, "bool");
            }
        }
        throw new Error("Unexpected operation or number type");
    }
    // Normal fallback
    const unary = new UnaryExpression(inner, op);
    return unary;
};
//# sourceMappingURL=UnaryExpression.js.map