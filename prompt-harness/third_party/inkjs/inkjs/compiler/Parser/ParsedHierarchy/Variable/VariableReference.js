"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.VariableReference = void 0;
const ContentList_1 = require("../ContentList");
const Expression_1 = require("../Expression/Expression");
const FlowBase_1 = require("../Flow/FlowBase");
const Path_1 = require("../Path");
const VariableReference_1 = require("../../../../engine/VariableReference");
const Weave_1 = require("../Weave");
const Identifier_1 = require("../Identifier");
const TypeAssertion_1 = require("../../../../engine/TypeAssertion");
class VariableReference extends Expression_1.Expression {
    // - Normal variables have a single item in their "path"
    // - Knot/stitch names for read counts are actual dot-separated paths
    //   (though this isn't actually used at time of writing)
    // - List names are dot separated: listName.itemName (or just itemName)
    get name() {
        return this.path.join(".");
    }
    get path() {
        return this.pathIdentifiers.map((id) => id.name).filter(TypeAssertion_1.filterUndef);
    }
    get identifier() {
        if (!this.pathIdentifiers || this.pathIdentifiers.length == 0) {
            return null;
        }
        const name = this.path.join(".");
        const id = new Identifier_1.Identifier(name);
        return id;
    }
    get runtimeVarRef() {
        return this._runtimeVarRef;
    }
    constructor(pathIdentifiers) {
        super();
        this.pathIdentifiers = pathIdentifiers;
        this._runtimeVarRef = null;
        // Only known after GenerateIntoContainer has run
        this.isConstantReference = false;
        this.isListItemReference = false;
        this.GenerateIntoContainer = (container) => {
            let constantValue = this.story.constants.get(this.name);
            // If it's a constant reference, just generate the literal expression value
            // It's okay to access the constants at code generation time, since the
            // first thing the ExportRuntime function does it search for all the constants
            // in the story hierarchy, so they're all available.
            if (constantValue) {
                constantValue.GenerateConstantIntoContainer(container);
                this.isConstantReference = true;
                return;
            }
            this._runtimeVarRef = new VariableReference_1.VariableReference(this.name);
            // List item reference?
            // Path might be to a list (listName.listItemName or just listItemName)
            if (this.path.length === 1 || this.path.length === 2) {
                let listItemName = "";
                let listName = "";
                if (this.path.length === 1) {
                    listItemName = this.path[0];
                }
                else {
                    listName = this.path[0];
                    listItemName = this.path[1];
                }
                const listItem = this.story.ResolveListItem(listName, listItemName, this);
                if (listItem) {
                    this.isListItemReference = true;
                }
            }
            container.AddContent(this._runtimeVarRef);
        };
        this.toString = () => `{${this.path.join(".")}}`;
    }
    get typeName() {
        return "ref";
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        // Work is already done if it's a constant or list item reference
        if (this.isConstantReference || this.isListItemReference) {
            return;
        }
        // Is it a read count?
        const parsedPath = new Path_1.Path(this.pathIdentifiers);
        const targetForCount = parsedPath.ResolveFromContext(this);
        if (targetForCount) {
            if (!targetForCount.containerForCounting) {
                throw new Error();
            }
            targetForCount.containerForCounting.visitsShouldBeCounted = true;
            // If this is an argument to a function that wants a variable to be
            // passed by reference, then the Parsed.Divert will have generated a
            // Runtime.VariablePointerValue instead of allowing this object
            // to generate its RuntimeVariableReference. This only happens under
            // error condition since we shouldn't be passing a read count by
            // reference, but we don't want it to crash!
            if (this._runtimeVarRef === null) {
                return;
            }
            this._runtimeVarRef.pathForCount = targetForCount.runtimePath;
            this._runtimeVarRef.name = null;
            // Check for very specific writer error: getting read count and
            // printing it as content rather than as a piece of logic
            // e.g. Writing {myFunc} instead of {myFunc()}
            let targetFlow = (0, TypeAssertion_1.asOrNull)(targetForCount, FlowBase_1.FlowBase);
            if (targetFlow && targetFlow.isFunction) {
                // Is parent context content rather than logic?
                if (this.parent instanceof Weave_1.Weave ||
                    this.parent instanceof ContentList_1.ContentList ||
                    this.parent instanceof FlowBase_1.FlowBase) {
                    this.Warning(`'${targetFlow.identifier}' being used as read count rather than being called as function. Perhaps you intended to write ${targetFlow.identifier}()`);
                }
            }
            return;
        }
        // Couldn't find this multi-part path at all, whether as a divert
        // target or as a list item reference.
        if (this.path.length > 1) {
            let errorMsg = `Could not find target for read count: ${parsedPath}`;
            if (this.path.length <= 2) {
                errorMsg += `, or couldn't find list item with the name ${this.path.join(",")}`;
            }
            this.Error(errorMsg);
            return;
        }
        if (!context.ResolveVariableWithName(this.name, this).found) {
            this.Error(`Unresolved variable: ${this.name}`, this);
        }
    }
}
exports.VariableReference = VariableReference;
//# sourceMappingURL=VariableReference.js.map