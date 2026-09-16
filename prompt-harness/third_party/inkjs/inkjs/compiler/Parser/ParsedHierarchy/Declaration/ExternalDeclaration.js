"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ExternalDeclaration = void 0;
const Object_1 = require("../Object");
class ExternalDeclaration extends Object_1.ParsedObject {
    get name() {
        var _a;
        return ((_a = this.identifier) === null || _a === void 0 ? void 0 : _a.name) || null;
    }
    constructor(identifier, argumentNames) {
        super();
        this.identifier = identifier;
        this.argumentNames = argumentNames;
        this.GenerateRuntimeObject = () => {
            this.story.AddExternal(this);
            // No runtime code exists for an external, only metadata
            return null;
        };
    }
    get typeName() {
        return "EXTERNAL";
    }
    toString() {
        var _a;
        return `EXTERNAL ${(_a = this.identifier) === null || _a === void 0 ? void 0 : _a.name}`;
    }
}
exports.ExternalDeclaration = ExternalDeclaration;
//# sourceMappingURL=ExternalDeclaration.js.map