import { ListDefinition as RuntimeListDefinition } from "../../../../engine/ListDefinition";
import { ListElementDefinition } from "./ListElementDefinition";
import { ListValue } from "../../../../engine/Value";
import { ParsedObject } from "../Object";
import { Story } from "../Story";
import { VariableAssignment } from "../Variable/VariableAssignment";
import { Identifier } from "../Identifier";
export declare class ListDefinition extends ParsedObject {
    itemDefinitions: ListElementDefinition[];
    identifier: Identifier | null;
    variableAssignment: VariableAssignment | null;
    get typeName(): string;
    private _elementsByName;
    get runtimeListDefinition(): RuntimeListDefinition;
    readonly ItemNamed: (itemName: string) => ListElementDefinition | null;
    constructor(itemDefinitions: ListElementDefinition[]);
    readonly GenerateRuntimeObject: () => ListValue;
    ResolveReferences(context: Story): void;
}
