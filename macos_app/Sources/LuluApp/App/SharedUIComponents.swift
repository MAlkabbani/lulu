import SwiftUI

enum StatusTone {
    case neutral
    case info
    case success
    case warning
    case danger

    var color: Color {
        switch self {
        case .neutral:
            return .secondary
        case .info:
            return .blue
        case .success:
            return .green
        case .warning:
            return .orange
        case .danger:
            return .red
        }
    }
}

struct StatusBadge: View {
    let text: String
    let tone: StatusTone

    var body: some View {
        Text(text)
            .font(.caption)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(tone.color.opacity(0.15), in: Capsule())
            .foregroundStyle(tone.color)
            .accessibilityLabel(text)
    }
}

struct InlineNotice: View {
    let text: String
    let tone: StatusTone
    var systemImage: String

    init(_ text: String, tone: StatusTone, systemImage: String? = nil) {
        self.text = text
        self.tone = tone
        self.systemImage = systemImage ?? {
            switch tone {
            case .success:
                return "checkmark.circle.fill"
            case .warning:
                return "exclamationmark.triangle.fill"
            case .danger:
                return "xmark.octagon.fill"
            case .info, .neutral:
                return "info.circle.fill"
            }
        }()
    }

    var body: some View {
        Label {
            Text(text)
                .frame(maxWidth: .infinity, alignment: .leading)
        } icon: {
            Image(systemName: systemImage)
        }
        .font(.callout)
        .foregroundStyle(tone.color)
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tone.color.opacity(0.1), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

struct EmptyStateView: View {
    let text: String

    var body: some View {
        Text(text)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel(text)
    }
}

struct LabeledValueRow: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .foregroundStyle(.secondary)
            Text(value)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fontWeight(.medium)
                .textSelection(.enabled)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label): \(value)")
    }
}

struct LabeledTextFieldRow: View {
    let label: String
    let prompt: String
    @Binding var text: String
    var helpText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .foregroundStyle(.secondary)
            TextField(prompt, text: $text)
                .textFieldStyle(.roundedBorder)
                .accessibilityLabel(label)
                .help(helpText ?? "")
            if let helpText, !helpText.isEmpty {
                Text(helpText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct ChecklistItem {
    let title: String
    let status: String
    let detail: String
    let tone: StatusTone
}

struct ChecklistRow: View {
    let item: ChecklistItem

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            StatusBadge(text: item.status, tone: item.tone)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 4) {
                Text(item.title)
                    .fontWeight(.semibold)
                Text(item.detail)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(item.title). \(item.status). \(item.detail)")
    }
}
