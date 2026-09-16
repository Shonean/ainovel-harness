import { IFileHandler } from "../IFileHandler";
export declare class DefaultFileHandler implements IFileHandler {
    readonly rootPath?: string | undefined;
    constructor(rootPath?: string | undefined);
    readonly ResolveInkFilename: () => string;
    readonly LoadInkFileContents: () => string;
}
