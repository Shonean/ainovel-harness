import { Container as RuntimeContainer } from "../../../../engine/Container";
import { Expression } from "./Expression";
export declare class MultipleConditionExpression extends Expression {
    get subExpressions(): Expression[];
    constructor(conditionExpressions: Expression[]);
    get typeName(): string;
    readonly GenerateIntoContainer: (container: RuntimeContainer) => void;
}
