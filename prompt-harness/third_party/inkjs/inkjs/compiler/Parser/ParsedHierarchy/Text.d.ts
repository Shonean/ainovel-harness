import { ParsedObject } from "./Object";
import { InkObject as RuntimeObject } from "../../../engine/Object";
export declare class Text extends ParsedObject {
    text: string;
    constructor(text: string);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject;
    readonly toString: () => string;
}
