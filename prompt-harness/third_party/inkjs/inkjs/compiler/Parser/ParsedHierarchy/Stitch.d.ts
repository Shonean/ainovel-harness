import { Argument } from "./Argument";
import { FlowBase } from "./Flow/FlowBase";
import { FlowLevel } from "./Flow/FlowLevel";
import { Identifier } from "./Identifier";
import { ParsedObject } from "./Object";
export declare class Stitch extends FlowBase {
    get flowLevel(): FlowLevel;
    constructor(name: Identifier, topLevelObjects: ParsedObject[], args: Argument[], isFunction: boolean);
    get typeName(): string;
    private baseToString;
    toString: () => string;
}
