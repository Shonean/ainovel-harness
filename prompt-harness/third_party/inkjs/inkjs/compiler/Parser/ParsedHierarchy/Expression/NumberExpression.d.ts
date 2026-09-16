import { Container as RuntimeContainer } from "../../../../engine/Container";
import { Expression } from "./Expression";
import { ParsedObject } from "../Object";
export declare class NumberExpression extends Expression {
    value: number | boolean;
    subtype: "int" | "float" | "bool";
    constructor(value: number | boolean, subtype: "int" | "float" | "bool");
    get typeName(): string;
    isInt: () => boolean;
    isFloat: () => boolean;
    isBool: () => boolean;
    readonly GenerateIntoContainer: (container: RuntimeContainer) => void;
    readonly toString: () => string;
    Equals(obj: ParsedObject): boolean;
}
