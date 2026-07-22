import Foundation

public class PromptsEngine {
    
    public struct PromptSpec: Codable {
        public let version: String
        public let defaultMode: String
        public let hardContracts: [String]
        public let systemPrompts: [String: String]

        enum CodingKeys: String, CodingKey {
            case version
            case defaultMode = "default_mode"
            case hardContracts = "hard_contracts"
            case systemPrompts = "system_prompts"
        }
    }
    
    private var spec: PromptSpec?
    
    public init(jsonURL: URL) {
        if let data = try? Data(contentsOf: jsonURL) {
            let decoder = JSONDecoder()
            self.spec = try? decoder.decode(PromptSpec.self, from: data)
        }
    }
    
    public func buildSystemPrompt(mode: String, dictionary: [String] = []) -> String {
        guard let spec = spec else { return "" }
        let selectedMode = spec.systemPrompts[mode] ?? spec.systemPrompts["correct"] ?? ""
        
        var fullPrompt = selectedMode + "\n\nHard Contract & Cleanup Rules:\n"
        for rule in spec.hardContracts {
            fullPrompt += "- \(rule)\n"
        }
        
        if !dictionary.isEmpty {
            let vocabStr = dictionary.joined(separator: ", ")
            fullPrompt += "\n\nAuthoritative Context Vocabulary & Proper Nouns: \(vocabStr)"
        }
        
        return fullPrompt
    }
}
