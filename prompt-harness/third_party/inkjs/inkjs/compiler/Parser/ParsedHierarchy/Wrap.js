"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Wrap = void 0;
const Object_1 = require("./Object");
class Wrap extends Object_1.ParsedObject {
    constructor(_objToWrap) {
        super();
        this._objToWrap = _objToWrap;
        this.GenerateRuntimeObject = () => this._objToWrap;
    }
}
exports.Wrap = Wrap;
//# sourceMappingURL=Wrap.js.map