import { Container as RuntimeContainer } from "../../../../engine/Container";
import { Expression } from "./Expression";
export declare class UnaryExpression extends Expression {
    readonly op: string;
    get nativeNameForOp(): string;
    innerExpression: Expression;
    static readonly WithInner: (inner: Expression, op: string) => Expression;
    constructor(inner: Expression, op: string);
    get typeName(): string;
    readonly GenerateIntoContainer: (container: RuntimeContainer) => void;
    readonly toString: () => string;
}
