import { Argument } from "./ParsedHierarchy/Argument";
import { Identifier } from "./ParsedHierarchy/Identifier";
export declare class FlowDecl {
    readonly name: Identifier;
    readonly args: Argument[];
    readonly isFunction: boolean;
    constructor(name: Identifier, args: Argument[], isFunction: boolean);
}
