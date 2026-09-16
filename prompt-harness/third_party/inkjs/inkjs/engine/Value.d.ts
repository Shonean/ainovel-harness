import { InkObject } from "./Object";
import { Path } from "./Path";
import { InkList, InkListItem } from "./InkList";
import { StoryException } from "./StoryException";
export declare abstract class AbstractValue extends InkObject {
    abstract get valueType(): ValueType;
    abstract get isTruthy(): boolean;
    abstract get valueObject(): any;
    abstract Cast(newType: ValueType): Value<any>;
    static Create(val: any, preferredNumberType?: ValueType): Value<any> | null;
    Copy(): InkObject;
    BadCastException(targetType: ValueType): StoryException;
}
export declare abstract class Value<T extends {
    toString: () => string;
}> extends AbstractValue {
    value: T | null;
    constructor(val: T | null);
    get valueObject(): T | null;
    toString(): string;
}
export declare class BoolValue extends Value<boolean> {
    constructor(val: boolean);
    get isTruthy(): boolean;
    get valueType(): ValueType;
    Cast(newType: ValueType): Value<any>;
    toString(): "false" | "true";
}
export declare class IntValue extends Value<number> {
    constructor(val: number);
    get isTruthy(): boolean;
    get valueType(): ValueType;
    Cast(newType: ValueType): Value<any>;
}
export declare class FloatValue extends Value<number> {
    constructor(val: number);
    get isTruthy(): boolean;
    get valueType(): ValueType;
    Cast(newType: ValueType): Value<any>;
}
export declare class StringValue extends Value<string> {
    _isNewline: boolean;
    _isInlineWhitespace: boolean;
    constructor(val: string);
    get valueType(): ValueType;
    get isTruthy(): boolean;
    get isNewline(): boolean;
    get isInlineWhitespace(): boolean;
    get isNonWhitespace(): boolean;
    Cast(newType: ValueType): Value<any>;
}
export declare class DivertTargetValue extends Value<Path> {
    constructor(targetPath?: Path | null);
    get valueType(): ValueType;
    get targetPath(): Path;
    set targetPath(value: Path);
    get isTruthy(): never;
    Cast(newType: ValueType): Value<any>;
    toString(): string;
}
export declare class VariablePointerValue extends Value<string> {
    _contextIndex: number;
    constructor(variableName: string, contextIndex?: number);
    get contextIndex(): number;
    set contextIndex(value: number);
    get variableName(): string;
    set variableName(value: string);
    get valueType(): ValueType;
    get isTruthy(): never;
    Cast(newType: ValueType): Value<any>;
    toString(): string;
    Copy(): VariablePointerValue;
}
export declare class ListValue extends Value<InkList> {
    get isTruthy(): boolean;
    get valueType(): ValueType;
    Cast(newType: ValueType): Value<any>;
    constructor();
    constructor(list: InkList);
    constructor(listOrSingleItem: InkListItem, singleValue: number);
    static RetainListOriginsForAssignment(oldValue: InkObject | null, newValue: InkObject): undefined;
}
export declare enum ValueType {
    Bool = -1,
    Int = 0,
    Float = 1,
    List = 2,
    String = 3,
    DivertTarget = 4,
    VariablePointer = 5
}
