import { Container as RuntimeContainer } from "../../../../engine/Container";
import { INamedContent } from "../../../../engine/INamedContent";
import { IWeavePoint } from "../IWeavePoint";
import { ParsedObject } from "../Object";
import { InkObject as RuntimeObject } from "../../../../engine/Object";
import { Story } from "../Story";
import { Identifier } from "../Identifier";
export declare class Gather extends ParsedObject implements INamedContent, IWeavePoint {
    readonly indentationDepth: number;
    get name(): string | null;
    identifier?: Identifier;
    get runtimeContainer(): RuntimeContainer;
    constructor(identifier: Identifier | null, indentationDepth: number);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject;
    ResolveReferences(context: Story): void;
    readonly toString: () => string;
}
