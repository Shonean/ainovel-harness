import { Container as RuntimeContainer } from "../../../../engine/Container";
import { Expression } from "../Expression/Expression";
import { Story } from "../Story";
import { VariableReference as RuntimeVariableReference } from "../../../../engine/VariableReference";
import { Identifier } from "../Identifier";
export declare class VariableReference extends Expression {
    readonly pathIdentifiers: Identifier[];
    private _runtimeVarRef;
    get name(): string;
    get path(): string[];
    get identifier(): Identifier | null;
    isConstantReference: boolean;
    isListItemReference: boolean;
    get runtimeVarRef(): RuntimeVariableReference | null;
    constructor(pathIdentifiers: Identifier[]);
    get typeName(): string;
    readonly GenerateIntoContainer: (container: RuntimeContainer) => void;
    ResolveReferences(context: Story): void;
    readonly toString: () => string;
}
