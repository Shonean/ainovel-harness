import { Container as RuntimeContainer } from "../../../../engine/Container";
import { Expression } from "../Expression/Expression";
import { Identifier } from "../Identifier";
export declare class List extends Expression {
    readonly itemIdentifierList: Identifier[];
    constructor(itemIdentifierList: Identifier[]);
    get typeName(): string;
    readonly GenerateIntoContainer: (container: RuntimeContainer) => void;
}
