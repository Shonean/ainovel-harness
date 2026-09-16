"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.List = void 0;
const Expression_1 = require("../Expression/Expression");
const InkList_1 = require("../../../../engine/InkList");
const InkList_2 = require("../../../../engine/InkList");
const Value_1 = require("../../../../engine/Value");
class List extends Expression_1.Expression {
    constructor(itemIdentifierList) {
        super();
        this.itemIdentifierList = itemIdentifierList;
        this.GenerateIntoContainer = (container) => {
            var _a, _b;
            const runtimeRawList = new InkList_1.InkList();
            if (this.itemIdentifierList != null) {
                for (const itemIdentifier of this.itemIdentifierList) {
                    const nameParts = ((_a = itemIdentifier === null || itemIdentifier === void 0 ? void 0 : itemIdentifier.name) === null || _a === void 0 ? void 0 : _a.split(".")) || [];
                    let listName = null;
                    let listItemName = "";
                    if (nameParts.length > 1) {
                        listName = nameParts[0];
                        listItemName = nameParts[1];
                    }
                    else {
                        listItemName = nameParts[0];
                    }
                    const listItem = this.story.ResolveListItem(listName, listItemName, this);
                    if (listItem === null) {
                        if (listName === null) {
                            this.Error(`Could not find list definition that contains item '${itemIdentifier}'`);
                        }
                        else {
                            this.Error(`Could not find list item ${itemIdentifier}`);
                        }
                    }
                    else {
                        if (listItem.parent == null) {
                            this.Error(`Could not find list definition for item ${itemIdentifier}`);
                            return;
                        }
                        if (!listName) {
                            listName = ((_b = listItem.parent.identifier) === null || _b === void 0 ? void 0 : _b.name) || null;
                        }
                        const item = new InkList_2.InkListItem(listName, listItem.name || null);
                        if (runtimeRawList.has(item.serialized())) {
                            this.Warning(`Duplicate of item '${itemIdentifier}' in list.`);
                        }
                        else {
                            runtimeRawList.Add(item, listItem.seriesValue);
                        }
                    }
                }
            }
            container.AddContent(new Value_1.ListValue(runtimeRawList));
        };
    }
    get typeName() {
        return "List";
    }
}
exports.List = List;
//# sourceMappingURL=List.js.map