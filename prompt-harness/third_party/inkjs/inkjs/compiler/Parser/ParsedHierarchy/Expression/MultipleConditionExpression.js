"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MultipleConditionExpression = void 0;
const Expression_1 = require("./Expression");
const NativeFunctionCall_1 = require("../../../../engine/NativeFunctionCall");
class MultipleConditionExpression extends Expression_1.Expression {
    get subExpressions() {
        return this.content;
    }
    constructor(conditionExpressions) {
        super();
        this.GenerateIntoContainer = (container) => {
            //    A && B && C && D
            // => (((A B &&) C &&) D &&) etc
            let isFirst = true;
            for (const conditionExpr of this.subExpressions) {
                conditionExpr.GenerateIntoContainer(container);
                if (!isFirst) {
                    container.AddContent(NativeFunctionCall_1.NativeFunctionCall.CallWithName("&&"));
                }
                isFirst = false;
            }
        };
        this.AddContent(conditionExpressions);
    }
    get typeName() {
        return "MultipleConditionExpression";
    }
}
exports.MultipleConditionExpression = MultipleConditionExpression;
//# sourceMappingURL=MultipleConditionExpression.js.map