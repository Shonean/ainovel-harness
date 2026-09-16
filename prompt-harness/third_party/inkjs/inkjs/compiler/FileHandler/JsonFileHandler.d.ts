import { IFileHandler } from "../IFileHandler";
export declare class JsonFileHandler implements IFileHandler {
    readonly fileHierarchy: Record<string, string>;
    constructor(fileHierarchy: Record<string, string>);
    readonly ResolveInkFilename: (filename: string) => string;
    readonly LoadInkFileContents: (filename: string) => string;
}
