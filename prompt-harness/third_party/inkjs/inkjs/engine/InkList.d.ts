import { ListDefinition } from "./ListDefinition";
import { Story } from "./Story";
export declare class InkListItem implements IInkListItem {
    readonly originName: string | null;
    readonly itemName: string | null;
    constructor(originName: string | null, itemName: string | null);
    constructor(fullName: string | null);
    static get Null(): InkListItem;
    get isNull(): boolean;
    get fullName(): string;
    toString(): string;
    Equals(obj: InkListItem): boolean;
    /**
     * Returns a shallow clone of the current instance.
     */
    copy(): InkListItem;
    /**
     * Returns a `SerializedInkListItem` representing the current
     * instance. The result is intended to be used as a key inside a Map.
     */
    serialized(): SerializedInkListItem;
    /**
     * Reconstructs a `InkListItem` from the given SerializedInkListItem.
     */
    static fromSerializedKey(key: SerializedInkListItem): InkListItem;
    /**
     * Determines whether the given item is sufficiently `InkListItem`-like
     * to be used as a template when reconstructing the InkListItem.
     */
    private static isLikeInkListItem;
}
export declare class InkList extends Map<SerializedInkListItem, number> {
    origins: ListDefinition[] | null;
    _originNames: string[] | null;
    constructor();
    constructor(otherList: InkList);
    constructor(singleOriginListName: string, originStory: Story);
    constructor(singleElement: KeyValuePair<InkListItem, number>);
    static FromString(myListItem: string, originStory: Story): InkList;
    AddItem(itemOrItemName: InkListItem | string | null, storyObject?: Story | null): undefined;
    ContainsItemNamed(itemName: string | null): boolean;
    ContainsKey(key: InkListItem): boolean;
    Add(key: InkListItem, value: number): void;
    Remove(key: InkListItem): boolean;
    get Count(): number;
    get originOfMaxItem(): ListDefinition | null;
    get originNames(): string[];
    SetInitialOriginName(initialOriginName: string): void;
    SetInitialOriginNames(initialOriginNames: string[]): void;
    get maxItem(): KeyValuePair<InkListItem, number>;
    get minItem(): KeyValuePair<InkListItem, number>;
    get inverse(): InkList;
    get all(): InkList;
    Union(otherList: InkList): InkList;
    Intersect(otherList: InkList): InkList;
    HasIntersection(otherList: InkList): boolean;
    Without(listToRemove: InkList): InkList;
    Contains(key: string): boolean;
    Contains(otherList: InkList): boolean;
    GreaterThan(otherList: InkList): boolean;
    GreaterThanOrEquals(otherList: InkList): boolean;
    LessThan(otherList: InkList): boolean;
    LessThanOrEquals(otherList: InkList): boolean;
    MaxAsList(): InkList;
    MinAsList(): InkList;
    ListWithSubRange(minBound: any, maxBound: any): InkList;
    Equals(otherInkList: InkList): boolean;
    get orderedItems(): KeyValuePair<InkListItem, number>[];
    get singleItem(): InkListItem | null;
    toString(): string;
    valueOf(): number;
}
/**
 * In the original C# code, `InkListItem` was defined as value type, meaning
 * that two `InkListItem` would be considered equal as long as they held the
 * same values. This doesn't hold true in Javascript, as `InkListItem` is a
 * reference type (Javascript doesn't allow the creation of custom value types).
 *
 * The key equality of Map objects is based on the "SameValueZero" algorithm;
 * since `InkListItem` is a value type, two keys will only be considered
 * equal if they are, in fact, the same object. As we are trying to emulate
 * the original behavior as close as possible, this will lead to unforeseen
 * side effects.
 *
 * In order to have a key equality based on value semantics, we'll convert
 * `InkListItem` to a valid string representation and use this representation
 * as a key (strings are value types in Javascript). Rather than using the
 * type `string` directly, we'll alias it to `SerializedInkListItem` and use
 * this type as the key for our Map-based `InkList`.
 *
 * Reducing `InkListItem` to a JSON representation would not be bulletproof
 * in the general case, but for our needs it works well. The major downside of
 * this method is that we will have to to reconstruct the original `InkListItem`
 * every time we'll need to access its properties.
 */
export type SerializedInkListItem = string;
/**
 * An interface inherited by `InkListItem`, defining exposed
 * properties. It's mainly used when deserializing a `InkListItem` from its
 * key (`SerializedInkListItem`)
 */
interface IInkListItem {
    readonly originName: string | null;
    readonly itemName: string | null;
}
export interface KeyValuePair<K, V> {
    Key: K;
    Value: V;
}
export {};
