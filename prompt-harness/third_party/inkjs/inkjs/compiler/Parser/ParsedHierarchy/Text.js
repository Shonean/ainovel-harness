"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Text = void 0;
const Object_1 = require("./Object");
const Value_1 = require("../../../engine/Value");
class Text extends Object_1.ParsedObject {
    constructor(text) {
        super();
        this.text = text;
        this.GenerateRuntimeObject = () => new Value_1.StringValue(this.text);
        this.toString = () => this.text;
    }
    get typeName() {
        return "Text";
    }
}
exports.Text = Text;
//# sourceMappingURL=Text.js.map