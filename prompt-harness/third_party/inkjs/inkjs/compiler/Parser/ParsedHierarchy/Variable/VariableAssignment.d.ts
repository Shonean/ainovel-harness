import { Expression } from "../Expression/Expression";
import { ListDefinition } from "../List/ListDefinition";
import { ParsedObject } from "../Object";
import { InkObject as RuntimeObject } from "../../../../engine/Object";
import { Story } from "../Story";
import { Identifier } from "../Identifier";
export declare class VariableAssignment extends ParsedObject {
    private _runtimeAssignment;
    get variableName(): string;
    readonly variableIdentifier: Identifier;
    readonly expression: Expression | null;
    readonly listDefinition: ListDefinition | null;
    readonly isGlobalDeclaration: boolean;
    readonly isNewTemporaryDeclaration: boolean;
    get typeName(): "temp" | "LIST" | "VAR" | "variable assignment";
    get isDeclaration(): boolean;
    constructor({ assignedExpression, isGlobalDeclaration, isTemporaryNewDeclaration, listDef, variableIdentifier, }: {
        readonly assignedExpression?: Expression;
        readonly isGlobalDeclaration?: boolean;
        readonly isTemporaryNewDeclaration?: boolean;
        readonly listDef?: ListDefinition;
        readonly variableIdentifier: Identifier;
    });
    readonly GenerateRuntimeObject: () => RuntimeObject | null;
    ResolveReferences(context: Story): void;
    readonly toString: () => string;
}
