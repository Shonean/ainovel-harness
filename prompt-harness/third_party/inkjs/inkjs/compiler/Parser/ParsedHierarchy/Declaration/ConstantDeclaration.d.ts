import { Expression } from "../Expression/Expression";
import { ParsedObject } from "../Object";
import { InkObject as RuntimeObject } from "../../../../engine/Object";
import { Story } from "../Story";
import { Identifier } from "../Identifier";
export declare class ConstantDeclaration extends ParsedObject {
    get constantName(): string | undefined;
    constantIdentifier: Identifier;
    private _expression;
    get expression(): Expression;
    constructor(name: Identifier, assignedExpression: Expression);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject | null;
    ResolveReferences(context: Story): void;
}
