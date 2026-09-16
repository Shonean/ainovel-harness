"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.AuthorWarning = void 0;
const Object_1 = require("./Object");
class AuthorWarning extends Object_1.ParsedObject {
    constructor(warningMessage) {
        super();
        this.warningMessage = warningMessage;
        this.GenerateRuntimeObject = () => {
            this.Warning(this.warningMessage);
            return null;
        };
    }
    get typeName() {
        return "AuthorWarning";
    }
}
exports.AuthorWarning = AuthorWarning;
//# sourceMappingURL=AuthorWarning.js.map