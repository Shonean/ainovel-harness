"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Argument = void 0;
class Argument {
    constructor(identifier = null, isByReference = null, isDivertTarget = null) {
        this.identifier = identifier;
        this.isByReference = isByReference;
        this.isDivertTarget = isDivertTarget;
    }
    get typeName() {
        return "Argument";
    }
}
exports.Argument = Argument;
//# sourceMappingURL=Argument.js.map