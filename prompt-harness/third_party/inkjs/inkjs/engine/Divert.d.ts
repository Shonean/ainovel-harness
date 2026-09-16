import { Path } from "./Path";
import { PushPopType } from "./PushPop";
import { InkObject } from "./Object";
import { Pointer } from "./Pointer";
export declare class Divert extends InkObject {
    get targetPath(): Path | null;
    set targetPath(value: Path | null);
    _targetPath: Path | null;
    get targetPointer(): Pointer;
    _targetPointer: Pointer;
    get targetPathString(): string | null;
    set targetPathString(value: string | null);
    variableDivertName: string | null;
    get hasVariableTarget(): boolean;
    pushesToStack: boolean;
    stackPushType: PushPopType;
    isExternal: boolean;
    externalArgs: number;
    isConditional: boolean;
    constructor(stackPushType?: PushPopType);
    Equals(obj: Divert | null): boolean;
    toString(): string;
}
