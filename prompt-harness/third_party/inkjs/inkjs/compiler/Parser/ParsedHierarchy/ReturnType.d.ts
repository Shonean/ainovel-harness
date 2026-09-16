import { Expression } from "./Expression/Expression";
import { ParsedObject } from "./Object";
import { InkObject as RuntimeObject } from "../../../engine/Object";
export declare class ReturnType extends ParsedObject {
    returnedExpression: Expression | null;
    constructor(returnedExpression?: Expression | null);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject;
}
