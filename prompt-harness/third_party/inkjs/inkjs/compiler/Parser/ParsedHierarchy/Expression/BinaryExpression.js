"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.BinaryExpression = void 0;
const Expression_1 = require("./Expression");
const NativeFunctionCall_1 = require("../../../../engine/NativeFunctionCall");
const UnaryExpression_1 = require("./UnaryExpression");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
class BinaryExpression extends Expression_1.Expression {
    constructor(left, right, opName) {
        super();
        this.opName = opName;
        this.GenerateIntoContainer = (container) => {
            this.leftExpression.GenerateIntoContainer(container);
            this.rightExpression.GenerateIntoContainer(container);
            this.opName = this.NativeNameForOp(this.opName);
            container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName(this.opName));
        };
        this.NativeNameForOp = (opName) => {
            if (opName === "and") {
                return "&&";
            }
            else if (opName === "or") {
                return "||";
            }
            else if (opName === "mod") {
                return "%";
            }
            else if (opName === "has") {
                return "?";
            }
            else if (opName === "hasnt") {
                return "!?";
            }
            return opName;
        };
        this.toString = () => `(${this.leftExpression} ${this.opName} ${this.rightExpression})`;
        this.leftExpression = this.AddContent(left);
        this.rightExpression = this.AddContent(right);
        this.opName = opName;
    }
    get typeName() {
        return "BinaryExpression";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        // Check for the following case:
        //
        //    (not A) ? B
        //
        // Since this easy to accidentally do:
        //
        //    not A ? B
        //
        // when you intend:
        //
        //    not (A ? B)
        if (this.NativeNameForOp(this.opName) === "?") {
            const leftUnary = (0, TypeAssertion_1.asOrNull)(this.leftExpression, UnaryExpression_1.UnaryExpression);
            if (leftUnary !== null &&
                (leftUnary.op === "not" || leftUnary.op === "!")) {
                this.Error(`Using 'not' or '!' here negates '${leftUnary.innerExpression}' rather than the result of the '?' or 'has' operator. You need to add parentheses around the (A ? B) expression.`);
            }
        }
    }
}
exports.BinaryExpression = BinaryExpression;
//# sourceMappingURL=BinaryExpression.js.map