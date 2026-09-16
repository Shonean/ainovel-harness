import { INamedContent } from "../../../../engine/INamedContent";
import { ParsedObject } from "../Object";
import { InkObject as RuntimeObject } from "../../../../engine/Object";
import { Identifier } from "../Identifier";
export declare class ExternalDeclaration extends ParsedObject implements INamedContent {
    readonly identifier: Identifier;
    readonly argumentNames: string[];
    get name(): string | null;
    constructor(identifier: Identifier, argumentNames: string[]);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject | null;
    toString(): string;
}
