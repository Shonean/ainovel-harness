import { CharacterSet } from "./CharacterSet";
import { StringParser } from "./StringParser/StringParser";
export declare class CommentEliminator extends StringParser {
    _commentOrNewlineStartCharacter: CharacterSet;
    _commentBlockEndCharacter: CharacterSet;
    _newlineCharacters: CharacterSet;
    readonly Process: () => string;
    readonly MainInk: () => string;
    readonly CommentsAndNewlines: () => string | null;
    readonly ParseSingleComment: () => import("./StringParser/StringParser").ParseRuleReturn;
    readonly EndOfLineComment: () => "" | null;
    readonly BlockComment: () => string | null;
    PreProcessInputString(str: string): string;
}
