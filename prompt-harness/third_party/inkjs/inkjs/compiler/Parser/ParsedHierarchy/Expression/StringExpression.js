"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.StringExpression = void 0;
const ControlCommand_1 = require("../../../../engine/ControlCommand");
const Expression_1 = require("./Expression");
const Text_1 = require("../Text");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
class StringExpression extends Expression_1.Expression {
    get isSingleString() {
        if (this.content.length !== 1) {
            return false;
        }
        const c = this.content[0];
        if (!(c instanceof Text_1.Text)) {
            return false;
        }
        return true;
    }
    constructor(content) {
        super();
        this.GenerateIntoContainer = (container) => {
            container.AddContent(ControlCommand_1.ControlCommand.BeginString());
            for (const c of this.content) {
                container.AddContent(c.runtimeObject);
            }
            container.AddContent(ControlCommand_1.ControlCommand.EndString());
        };
        this.toString = () => {
            let sb = "";
            for (const c of this.content) {
                sb += c;
            }
            return sb;
        };
        this.AddContent(content);
    }
    get typeName() {
        return "String";
    }
    // Equals override necessary in order to check for CONST multiple definition equality
    Equals(obj) {
        const otherStr = (0, TypeAssertion_1.asOrNull)(obj, StringExpression);
        if (otherStr === null) {
            return false;
        }
        // Can only compare direct equality on single strings rather than
        // complex string expressions that contain dynamic logic
        if (!this.isSingleString || !otherStr.isSingleString) {
            return false;
        }
        const thisTxt = this.toString();
        const otherTxt = otherStr.toString();
        return thisTxt === otherTxt;
    }
}
exports.StringExpression = StringExpression;
//# sourceMappingURL=StringExpression.js.map