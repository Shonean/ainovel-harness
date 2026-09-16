import { ParsedObject } from "./Object";
import { InkObject as RuntimeObject } from "../../../engine/Object";
export declare class Wrap<T extends RuntimeObject> extends ParsedObject {
    private _objToWrap;
    constructor(_objToWrap: T);
    readonly GenerateRuntimeObject: () => RuntimeObject;
}
