"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ListDefinition = void 0;
const InkList_1 = require("../../../../engine/InkList");
const InkList_2 = require("../../../../engine/InkList");
const ListDefinition_1 = require("../../../../engine/ListDefinition");
const Value_1 = require("../../../../engine/Value");
const Object_1 = require("../Object");
const SymbolType_1 = require("../SymbolType");
class ListDefinition extends Object_1.ParsedObject {
    get typeName() {
        return "ListDefinition";
    }
    get runtimeListDefinition() {
        var _a;
        const allItems = new Map();
        for (const e of this.itemDefinitions) {
            if (!allItems.has(e.name)) {
                allItems.set(e.name, e.seriesValue);
            }
            else {
                this.Error(`List '${this.identifier}' contains duplicate items called '${e.name}'`);
            }
        }
        return new ListDefinition_1.ListDefinition(((_a = this.identifier) === null || _a === void 0 ? void 0 : _a.name) || "", allItems);
    }
    constructor(itemDefinitions) {
        super();
        this.itemDefinitions = itemDefinitions;
        this.identifier = null;
        this.variableAssignment = null;
        this._elementsByName = null;
        this.ItemNamed = (itemName) => {
            if (this._elementsByName === null) {
                this._elementsByName = new Map();
                for (const el of this.itemDefinitions) {
                    this._elementsByName.set(el.name, el);
                }
            }
            const foundElement = this._elementsByName.get(itemName) || null;
            return foundElement;
        };
        this.GenerateRuntimeObject = () => {
            var _a, _b;
            const initialValues = new InkList_1.InkList();
            for (const itemDef of this.itemDefinitions) {
                if (itemDef.inInitialList) {
                    const item = new InkList_2.InkListItem(((_a = this.identifier) === null || _a === void 0 ? void 0 : _a.name) || null, itemDef.name || null);
                    initialValues.Add(item, itemDef.seriesValue);
                }
            }
            // Set origin name, so
            initialValues.SetInitialOriginName(((_b = this.identifier) === null || _b === void 0 ? void 0 : _b.name) || "");
            return new Value_1.ListValue(initialValues);
        };
        let currentValue = 1;
        for (const e of this.itemDefinitions) {
            if (e.explicitValue !== null) {
                currentValue = e.explicitValue;
            }
            e.seriesValue = currentValue;
            currentValue += 1;
        }
        this.AddContent(itemDefinitions);
    }
    ResolveReferences(context) {
        super.ResolveReferences(context);
        context.CheckForNamingCollisions(this, this.identifier, SymbolType_1.SymbolType.List);
    }
}
exports.ListDefinition = ListDefinition;
//# sourceMappingURL=ListDefinition.js.map