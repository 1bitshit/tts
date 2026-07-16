#[derive(Debug, Clone, PartialEq)]
pub enum Span {
    Speech {
        emotion: Option<String>,
        text: String,
    },
    Pause {
        milliseconds: u32,
    },
    Paralinguistic {
        kind: String,
    },
}

pub fn parse(input: &str) -> Vec<Span> {
    let mut spans = Vec::new();
    let mut emotion: Option<String> = None;
    let mut cursor = 0;
    while let Some(relative) = input[cursor..].find('[') {
        let start = cursor + relative;
        if start > cursor {
            let text = input[cursor..start].trim();
            if !text.is_empty() {
                spans.push(Span::Speech {
                    emotion: emotion.clone(),
                    text: text.into(),
                });
            }
        }
        let Some(end_relative) = input[start..].find(']') else {
            break;
        };
        let end = start + end_relative;
        let tag = &input[start + 1..end];
        if let Some(value) = tag
            .strip_prefix("pause:")
            .or_else(|| tag.strip_prefix("break:"))
        {
            let milliseconds = if let Some(ms) = value.strip_suffix("ms") {
                ms.parse().unwrap_or(350)
            } else if let Some(seconds) = value.strip_suffix('s') {
                (seconds.parse::<f32>().unwrap_or(0.35) * 1000.0) as u32
            } else {
                350
            };
            spans.push(Span::Pause { milliseconds });
        } else if matches!(tag, "laugh" | "sigh" | "huff") {
            spans.push(Span::Paralinguistic { kind: tag.into() });
        } else if tag == "neutral" {
            emotion = None;
        } else {
            emotion = Some(tag.into());
        }
        cursor = end + 1;
    }
    let tail = input[cursor..].trim();
    if !tail.is_empty() {
        spans.push(Span::Speech {
            emotion,
            text: tail.into(),
        });
    }
    spans
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parses_emotions_pauses_and_fillers() {
        assert_eq!(
            parse("[calm] Hallo. [pause:500ms] [fear] Wer ist da? [sigh]"),
            vec![
                Span::Speech {
                    emotion: Some("calm".into()),
                    text: "Hallo.".into()
                },
                Span::Pause { milliseconds: 500 },
                Span::Speech {
                    emotion: Some("fear".into()),
                    text: "Wer ist da?".into()
                },
                Span::Paralinguistic {
                    kind: "sigh".into()
                },
            ]
        );
    }
}
