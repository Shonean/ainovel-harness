"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.NumberExpression = void 0;
const Expression_1 = require("./Expression");
const Value_1 = require("../../../../engine/Value");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
// This class is named Number in the C# codebase
// but this conflict with the built-in Number class
class NumberExpression extends Expression_1.Expression {
    constructor(value, subtype) {
        super();
        this.isInt = () => this.subtype == "int";
        this.isFloat = () => this.subtype == "float";
        this.isBool = () => this.subtype == "bool";
        this.GenerateIntoContainer = (container) => {
            if (this.isInt()) {
                container.AddContent(new Value_1.IntValue(this.value));
            }
            else if (this.isFloat()) {
                container.AddContent(new Value_1.FloatValue(this.value));
            }
            else if (this.isBool()) {
                container.AddContent(new Value_1.BoolValue(this.value));
            }
        };
        this.toString = () => String(this.value);
        if ((typeof value === "number" && !Number.isNaN(value)) ||
            typeof value == "boolean") {
            this.value = value;
            this.subtype = subtype;
        }
        else {
            throw new Error("Unexpected object type in NumberExpression.");
        }
    }
    get typeName() {
        return "Number";
    }
    Equals(obj) {
        const numberExpression = (0, TypeAssertion_1.asOrNull)(obj, NumberExpression);
        if (!numberExpression)
            return false;
        return (numberExpression.subtype == this.subtype &&
            numberExpression.value == this.value);
    }
}
exports.NumberExpression = NumberExpression;
//# sourceMappingURL=NumberExpression.js.map