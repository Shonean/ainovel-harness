import { Container as RuntimeContainer } from "../../../../engine/Container";
import { Expression } from "./Expression";
import { Story } from "../Story";
import { Identifier } from "../Identifier";
export declare class IncDecExpression extends Expression {
    readonly varIdentifier: Identifier | null;
    private _runtimeAssignment;
    isInc: boolean;
    expression: Expression | null;
    constructor(varIdentifier: Identifier | null, isIncOrExpression: boolean | Expression, isInc?: boolean);
    get typeName(): string;
    readonly GenerateIntoContainer: (container: RuntimeContainer) => void;
    ResolveReferences(context: Story): void;
    get incrementDecrementWord(): "increment" | "decrement";
    readonly toString: () => string;
}
