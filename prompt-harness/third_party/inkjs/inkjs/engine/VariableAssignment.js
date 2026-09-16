"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.VariableAssignment = void 0;
const Object_1 = require("./Object");
class VariableAssignment extends Object_1.InkObject {
    constructor(variableName, isNewDeclaration) {
        super();
        this.variableName = variableName || null;
        this.isNewDeclaration = !!isNewDeclaration;
        this.isGlobal = false;
    }
    toString() {
        return "VarAssign to " + this.variableName;
    }
}
exports.VariableAssignment = VariableAssignment;
//# sourceMappingURL=VariableAssignment.js.map