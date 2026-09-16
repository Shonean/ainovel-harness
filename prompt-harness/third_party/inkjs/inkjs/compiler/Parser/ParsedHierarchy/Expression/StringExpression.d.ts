import { Container as RuntimeContainer } from "../../../../engine/Container";
import { Expression } from "./Expression";
import { ParsedObject } from "../Object";
export declare class StringExpression extends Expression {
    get isSingleString(): boolean;
    constructor(content: ParsedObject[]);
    get typeName(): string;
    readonly GenerateIntoContainer: (container: RuntimeContainer) => void;
    readonly toString: () => string;
    Equals(obj: ParsedObject): boolean;
}
