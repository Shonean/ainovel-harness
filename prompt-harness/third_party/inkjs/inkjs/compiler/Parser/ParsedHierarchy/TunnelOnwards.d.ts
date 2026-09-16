import { Divert } from "./Divert/Divert";
import { ParsedObject } from "./Object";
import { InkObject as RuntimeObject } from "../../../engine/Object";
import { Story } from "./Story";
export declare class TunnelOnwards extends ParsedObject {
    private _overrideDivertTarget;
    private _divertAfter;
    get divertAfter(): Divert | null;
    set divertAfter(value: Divert | null);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject;
    ResolveReferences(context: Story): void;
    toString: () => string;
}
