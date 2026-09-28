#pragma once

#include <JuceHeader.h>
#include "PluginProcessor.h"

//==============================================================================
// Maschine-style LookAndFeel
//==============================================================================

class MaschineEQLookAndFeel : public juce::LookAndFeel_V4
{
public:
    MaschineEQLookAndFeel();

    void drawRotarySlider(
        juce::Graphics& g,
        int x,
        int y,
        int width,
        int height,
        float sliderPosProportional,
        float rotaryStartAngle,
        float rotaryEndAngle,
        juce::Slider& slider) override;

    juce::Font getLabelFont(
        juce::Label& label) override;
};

//==============================================================================
// Main editor
//==============================================================================

class MaschineEQAudioProcessorEditor
    : public juce::AudioProcessorEditor
{
public:
    MaschineEQAudioProcessorEditor(
        MaschineEQAudioProcessor&);

    ~MaschineEQAudioProcessorEditor() override;

    void paint(
        juce::Graphics&) override;

    void resized() override;

private:
    MaschineEQAudioProcessor& audioProcessor;

    MaschineEQLookAndFeel maschineLookAndFeel;

    juce::TextButton infoButton { "i" };
    bool showingInfo = false;

    juce::Slider outputGainSlider;

    juce::Slider lowGainSlider;
    juce::Slider lowFreqSlider;

    juce::Slider lowMidGainSlider;
    juce::Slider lowMidFreqSlider;
    juce::Slider lowMidWidthSlider;

    juce::Slider highMidGainSlider;
    juce::Slider highMidFreqSlider;
    juce::Slider highMidWidthSlider;

    juce::Slider highGainSlider;
    juce::Slider highFreqSlider;

    juce::Label outputGainLabel;

    juce::Label lowGainLabel;
    juce::Label lowFreqLabel;

    juce::Label lowMidGainLabel;
    juce::Label lowMidFreqLabel;
    juce::Label lowMidWidthLabel;

    juce::Label highMidGainLabel;
    juce::Label highMidFreqLabel;
    juce::Label highMidWidthLabel;

    juce::Label highGainLabel;
    juce::Label highFreqLabel;

    using SliderAttachment =
        juce::AudioProcessorValueTreeState::SliderAttachment;

    std::unique_ptr<SliderAttachment> outputGainAttachment;

    std::unique_ptr<SliderAttachment> lowGainAttachment;
    std::unique_ptr<SliderAttachment> lowFreqAttachment;

    std::unique_ptr<SliderAttachment> lowMidGainAttachment;
    std::unique_ptr<SliderAttachment> lowMidFreqAttachment;
    std::unique_ptr<SliderAttachment> lowMidWidthAttachment;

    std::unique_ptr<SliderAttachment> highMidGainAttachment;
    std::unique_ptr<SliderAttachment> highMidFreqAttachment;
    std::unique_ptr<SliderAttachment> highMidWidthAttachment;

    std::unique_ptr<SliderAttachment> highGainAttachment;
    std::unique_ptr<SliderAttachment> highFreqAttachment;

    void configureSlider(
        juce::Slider& slider,
        juce::Label& label,
        const juce::String& labelText,
        const juce::String& suffix);

    void setEQControlsVisible(
        bool shouldBeVisible);

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(
        MaschineEQAudioProcessorEditor)
};
