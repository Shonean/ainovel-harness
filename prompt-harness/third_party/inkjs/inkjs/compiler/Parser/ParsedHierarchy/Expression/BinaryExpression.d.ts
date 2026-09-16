import { Container as RuntimeContainer } from "../../../../engine/Container";
import { Expression } from "./Expression";
import { Story } from "../Story";
export declare class BinaryExpression extends Expression {
    opName: string;
    readonly leftExpression: Expression;
    readonly rightExpression: Expression;
    constructor(left: Expression, right: Expression, opName: string);
    get typeName(): string;
    readonly GenerateIntoContainer: (container: RuntimeContainer) => void;
    ResolveReferences(context: Story): void;
    readonly NativeNameForOp: (opName: string) => string;
    readonly toString: () => string;
}
