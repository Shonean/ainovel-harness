import { Container as RuntimeContainer } from "../../../engine/Container";
import { ParsedObject } from "./Object";
import { InkObject as RuntimeObject } from "../../../engine/Object";
export declare class ContentList extends ParsedObject {
    dontFlatten: boolean;
    get runtimeContainer(): RuntimeContainer;
    constructor(objects?: ParsedObject[], ...moreObjects: ParsedObject[]);
    get typeName(): string;
    readonly TrimTrailingWhitespace: () => void;
    readonly GenerateRuntimeObject: () => RuntimeObject;
    toString: () => string;
}
