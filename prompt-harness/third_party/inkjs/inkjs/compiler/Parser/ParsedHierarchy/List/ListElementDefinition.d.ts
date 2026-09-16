import { ListDefinition } from "./ListDefinition";
import { ParsedObject } from "../Object";
import { InkObject as RuntimeObject } from "../../../../engine/Object";
import { Story } from "../Story";
import { Identifier } from "../Identifier";
export declare class ListElementDefinition extends ParsedObject {
    readonly indentifier: Identifier;
    readonly inInitialList: boolean;
    readonly explicitValue: number | null;
    seriesValue: number;
    parent: ListDefinition | null;
    get fullName(): string;
    get typeName(): string;
    get name(): string | null;
    constructor(indentifier: Identifier, inInitialList: boolean, explicitValue?: number | null);
    readonly GenerateRuntimeObject: () => RuntimeObject;
    ResolveReferences(context: Story): void;
    readonly toString: () => string;
}
