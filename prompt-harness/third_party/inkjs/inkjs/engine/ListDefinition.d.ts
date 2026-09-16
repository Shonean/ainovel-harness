import { InkListItem, SerializedInkListItem } from "./InkList";
import { TryGetResult } from "./TryGetResult";
export declare class ListDefinition {
    _name: string;
    _items: Map<SerializedInkListItem, number> | null;
    _itemNameToValues: Map<string, number>;
    constructor(name: string, items: Map<string, number> | null);
    get name(): string;
    get items(): Map<string, number>;
    ValueForItem(item: InkListItem): number;
    ContainsItem(item: InkListItem): boolean;
    ContainsItemWithName(itemName: string): boolean;
    TryGetItemWithValue(val: number, item: InkListItem): TryGetResult<InkListItem>;
    TryGetValueForItem(item: InkListItem, intVal: number): TryGetResult<number>;
}
