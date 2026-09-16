"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ListElementDefinition = void 0;
const Object_1 = require("../Object");
const SymbolType_1 = require("../SymbolType");
class ListElementDefinition extends Object_1.ParsedObject {
    get fullName() {
        var _a;
        const parentList = this.parent;
        if (parentList === null) {
            throw new Error("Can't get full name without a parent list.");
        }
        return `${(_a = parentList.identifier) === null || _a === void 0 ? void 0 : _a.name}.${this.name}`;
    }
    get typeName() {
        return "ListElement";
    }
    get name() {
        var _a;
        return ((_a = this.indentifier) === null || _a === void 0 ? void 0 : _a.name) || null;
    }
    constructor(indentifier, inInitialList, explicitValue = null) {
        super();
        this.indentifier = indentifier;
        this.inInitialList = inInitialList;
        this.explicitValue = explicitValue;
        this.seriesValue = 0;
        this.parent = null;
        this.GenerateRuntimeObject = () => {
            throw new Error("Not implemented.");
        };
        this.toString = () => this.fullName;
        this.parent = this.parent;
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        context.CheckForNamingCollisions(this, this.indentifier, SymbolType_1.SymbolType.ListItem);
    }
}
exports.ListElementDefinition = ListElementDefinition;
//# sourceMappingURL=ListElementDefinition.js.map