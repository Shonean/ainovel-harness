import { ParsedObject } from "./Object";
export declare class AuthorWarning extends ParsedObject {
    readonly warningMessage: string;
    constructor(warningMessage: string);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => null;
}
