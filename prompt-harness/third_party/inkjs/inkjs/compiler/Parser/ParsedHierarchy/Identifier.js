"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Identifier = void 0;
class Identifier {
    constructor(name) {
        this.debugMetadata = null;
        this.toString = () => this.name || "undefined identifer";
        this.name = name;
    }
    get typeName() {
        return "Identifier";
    }
    static Done() {
        return new Identifier("DONE");
    }
}
exports.Identifier = Identifier;
//# sourceMappingURL=Identifier.js.map