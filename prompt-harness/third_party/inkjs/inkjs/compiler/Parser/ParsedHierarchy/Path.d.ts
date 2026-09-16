import { FlowLevel } from "./Flow/FlowLevel";
import { Identifier } from "./Identifier";
import { ParsedObject } from "./Object";
export declare class Path {
    private _baseTargetLevel;
    private components;
    get baseTargetLevel(): FlowLevel | null;
    get baseLevelIsAmbiguous(): boolean;
    get firstComponent(): string | null;
    get numberOfComponents(): number;
    private _dotSeparatedComponents;
    get dotSeparatedComponents(): string;
    constructor(argOne: FlowLevel | Identifier[] | Identifier, argTwo?: Identifier[]);
    get typeName(): string;
    readonly toString: () => string;
    readonly ResolveFromContext: (context: ParsedObject) => ParsedObject | null;
    readonly ResolveBaseTarget: (originalContext: ParsedObject) => ParsedObject | null;
    readonly ResolveTailComponents: (rootTarget: ParsedObject) => ParsedObject | null;
    readonly GetChildFromContext: (context: ParsedObject, childName: string | null, minimumLevel: FlowLevel | null, forceDeepSearch?: boolean) => ParsedObject | null;
}
