"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ConstantDeclaration = void 0;
const Object_1 = require("../Object");
const SymbolType_1 = require("../SymbolType");
class ConstantDeclaration extends Object_1.ParsedObject {
    get constantName() {
        var _a;
        return (_a = this.constantIdentifier) === null || _a === void 0 ? void 0 : _a.name;
    }
    get expression() {
        if (!this._expression) {
            throw new Error();
        }
        return this._expression;
    }
    constructor(name, assignedExpression) {
        super();
        this._expression = null;
        this.GenerateRuntimeObject = () => {
            // Global declarations don't generate actual procedural
            // runtime objects, but instead add a global variable to the story itself.
            // The story then initialises them all in one go at the start of the game.
            return null;
        };
        this.constantIdentifier = name;
        // Defensive programming in case parsing of assignedExpression failed
        if (assignedExpression) {
            this._expression = this.AddContent(assignedExpression);
        }
    }
    get typeName() {
        return "CONST";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        context.CheckForNamingCollisions(this, this.constantIdentifier, SymbolType_1.SymbolType.Var);
    }
}
exports.ConstantDeclaration = ConstantDeclaration;
//# sourceMappingURL=ConstantDeclaration.js.map