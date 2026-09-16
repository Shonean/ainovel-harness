"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.VariableAssignment = void 0;
const Container_1 = require("../../../../engine/Container");
const ClosestFlowBase_1 = require("../Flow/ClosestFlowBase");
const ListDefinition_1 = require("../List/ListDefinition");
const Object_1 = require("../Object");
const SymbolType_1 = require("../SymbolType");
const VariableAssignment_1 = require("../../../../engine/VariableAssignment");
const VariableReference_1 = require("./VariableReference");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
class VariableAssignment extends Object_1.ParsedObject {
    get variableName() {
        return this.variableIdentifier.name;
    }
    get typeName() {
        if (this.isNewTemporaryDeclaration) {
            return "temp";
        }
        else if (this.isGlobalDeclaration) {
            if (this.listDefinition !== null) {
                return "LIST";
            }
            return "VAR";
        }
        return "variable assignment";
    }
    get isDeclaration() {
        return this.isGlobalDeclaration || this.isNewTemporaryDeclaration;
    }
    constructor({ assignedExpression, isGlobalDeclaration, isTemporaryNewDeclaration, listDef, variableIdentifier, }) {
        super();
        this._runtimeAssignment = null;
        this.expression = null;
        this.listDefinition = null;
        this.GenerateRuntimeObject = () => {
            let newDeclScope = null;
            if (this.isGlobalDeclaration) {
                newDeclScope = this.story;
            }
            else if (this.isNewTemporaryDeclaration) {
                newDeclScope = (0, ClosestFlowBase_1.ClosestFlowBase)(this);
            }
            if (newDeclScope) {
                newDeclScope.AddNewVariableDeclaration(this);
            }
            // Global declarations don't generate actual procedural
            // runtime objects, but instead add a global variable to the story itself.
            // The story then initialises them all in one go at the start of the game.
            if (this.isGlobalDeclaration) {
                return null;
            }
            const container = new Container_1.Container();
            // The expression's runtimeObject is actually another nested container
            if (this.expression) {
                container.AddContent(this.expression.runtimeObject);
            }
            else if (this.listDefinition) {
                container.AddContent(this.listDefinition.runtimeObject);
            }
            this._runtimeAssignment = new VariableAssignment_1.VariableAssignment(this.variableName, this.isNewTemporaryDeclaration);
            container.AddContent(this._runtimeAssignment);
            return container;
        };
        this.toString = () => `${this.isGlobalDeclaration
            ? "VAR"
            : this.isNewTemporaryDeclaration
                ? "~ temp"
                : ""} ${this.variableName}`;
        this.variableIdentifier = variableIdentifier;
        this.isGlobalDeclaration = Boolean(isGlobalDeclaration);
        this.isNewTemporaryDeclaration = Boolean(isTemporaryNewDeclaration);
        // Defensive programming in case parsing of assignedExpression failed
        if (listDef instanceof ListDefinition_1.ListDefinition) {
            this.listDefinition = this.AddContent(listDef);
            this.listDefinition.variableAssignment = this;
            // List definitions are always global
            this.isGlobalDeclaration = true;
        }
        else if (assignedExpression) {
            this.expression = this.AddContent(assignedExpression);
        }
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        // List definitions are checked for conflicts separately
        if (this.isDeclaration && this.listDefinition === null) {
            context.CheckForNamingCollisions(this, this.variableIdentifier, this.isGlobalDeclaration ? SymbolType_1.SymbolType.Var : SymbolType_1.SymbolType.Temp);
        }
        // Initial VAR x = [intialValue] declaration, not re-assignment
        if (this.isGlobalDeclaration) {
            const variableReference = (0, TypeAssertion_1.asOrNull)(this.expression, VariableReference_1.VariableReference);
            if (variableReference &&
                !variableReference.isConstantReference &&
                !variableReference.isListItemReference) {
                this.Error("global variable assignments cannot refer to other variables, only literal values, constants and list items");
            }
        }
        if (!this.isNewTemporaryDeclaration) {
            const resolvedVarAssignment = context.ResolveVariableWithName(this.variableName, this);
            if (!resolvedVarAssignment.found) {
                if (this.variableName in this.story.constants) {
                    this.Error(`Can't re-assign to a constant (do you need to use VAR when declaring '${this.variableName}'?)`, this);
                }
                else {
                    this.Error(`Variable could not be found to assign to: '${this.variableName}'`, this);
                }
            }
            // A runtime assignment may not have been generated if it's the initial global declaration,
            // since these are hoisted out and handled specially in Story.ExportRuntime.
            if (this._runtimeAssignment) {
                this._runtimeAssignment.isGlobal = resolvedVarAssignment.isGlobal;
            }
        }
    }
}
exports.VariableAssignment = VariableAssignment;
//# sourceMappingURL=VariableAssignment.js.map