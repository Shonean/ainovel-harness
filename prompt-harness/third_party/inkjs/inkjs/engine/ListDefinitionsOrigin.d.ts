import { ListValue } from "./Value";
import { ListDefinition } from "./ListDefinition";
import { TryGetResult } from "./TryGetResult";
export declare class ListDefinitionsOrigin {
    protected _lists: Map<string, ListDefinition>;
    protected _allUnambiguousListValueCache: Map<string, ListValue>;
    constructor(lists: ListDefinition[]);
    get lists(): ListDefinition[];
    TryListGetDefinition(name: string | null, def: ListDefinition | null): TryGetResult<ListDefinition | null>;
    FindSingleItemListWithName(name: string | null): ListValue | null;
}
