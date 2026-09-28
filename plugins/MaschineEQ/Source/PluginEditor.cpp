#include "PluginProcessor.h"
#include "PluginEditor.h"

//==============================================================================
// LookAndFeel
//==============================================================================

MaschineEQLookAndFeel::MaschineEQLookAndFeel()
{
    setColour(
        juce::Label::textColourId,
        juce::Colour::fromRGB(165, 165, 165));

    setColour(
        juce::Slider::rotarySliderFillColourId,
        juce::Colour::fromRGB(255, 105, 68));

    setColour(
        juce::Slider::rotarySliderOutlineColourId,
        juce::Colour::fromRGB(55, 55, 55));
}

//==============================================================================

juce::Font MaschineEQLookAndFeel::getLabelFont(
    juce::Label& label)
{
    juce::ignoreUnused(label);

    return juce::Font(
        juce::FontOptions(12.0f));
}

//==============================================================================

void MaschineEQLookAndFeel::drawRotarySlider(
    juce::Graphics& g,
    int x,
    int y,
    int width,
    int height,
    float sliderPosProportional,
    float rotaryStartAngle,
    float rotaryEndAngle,
    juce::Slider& slider)
{
    juce::ignoreUnused(slider);

    const float centreX =
        static_cast<float>(x)
        + static_cast<float>(width) * 0.5f;

    const float centreY =
        static_cast<float>(y)
        + static_cast<float>(height) * 0.5f;

    const float radius =
        juce::jmin(
            static_cast<float>(width),
            static_cast<float>(height))
        * 0.36f;

    const float angle =
        rotaryStartAngle
        + sliderPosProportional
        * (rotaryEndAngle - rotaryStartAngle);

    g.setColour(
        juce::Colour::fromRGB(29, 29, 29));

    g.fillEllipse(
        centreX - radius + 3.0f,
        centreY - radius + 3.0f,
        (radius - 3.0f) * 2.0f,
        (radius - 3.0f) * 2.0f);

    juce::Path backgroundArc;

    backgroundArc.addCentredArc(
        centreX,
        centreY,
        radius,
        radius,
        0.0f,
        rotaryStartAngle,
        rotaryEndAngle,
        true);

    g.setColour(
        juce::Colour::fromRGB(55, 55, 55));

    g.strokePath(
        backgroundArc,
        juce::PathStrokeType(
            5.0f,
            juce::PathStrokeType::curved,
            juce::PathStrokeType::rounded));

    if (sliderPosProportional > 0.001f)
    {
        juce::Path valueArc;

        valueArc.addCentredArc(
            centreX,
            centreY,
            radius,
            radius,
            0.0f,
            rotaryStartAngle,
            angle,
            true);

        g.setColour(
            juce::Colour::fromRGB(255, 105, 68));

        g.strokePath(
            valueArc,
            juce::PathStrokeType(
                5.0f,
                juce::PathStrokeType::curved,
                juce::PathStrokeType::rounded));
    }

    const float pointerInnerRadius =
        radius * 0.42f;

    const float pointerOuterRadius =
        radius * 0.73f;

    const float sinAngle =
        std::sin(angle);

    const float cosAngle =
        std::cos(angle);

    const juce::Point<float> innerPoint(
        centreX
            + pointerInnerRadius * sinAngle,
        centreY
            - pointerInnerRadius * cosAngle);

    const juce::Point<float> outerPoint(
        centreX
            + pointerOuterRadius * sinAngle,
        centreY
            - pointerOuterRadius * cosAngle);

    g.setColour(
        juce::Colour::fromRGB(175, 175, 175));

    g.drawLine(
        juce::Line<float>(
            innerPoint,
            outerPoint),
        3.0f);
}

//==============================================================================
// Editor construction
//==============================================================================

MaschineEQAudioProcessorEditor::
MaschineEQAudioProcessorEditor(
    MaschineEQAudioProcessor& p)

    : AudioProcessorEditor(&p),
      audioProcessor(p)
{
    setSize(700, 300);

    setLookAndFeel(
        &maschineLookAndFeel);

    infoButton.setTooltip(
        "About Maschine EQ Unofficial Remake");

    infoButton.setColour(
        juce::TextButton::buttonColourId,
        juce::Colour::fromRGB(45, 45, 45));

    infoButton.setColour(
        juce::TextButton::buttonOnColourId,
        juce::Colour::fromRGB(255, 105, 68));

    infoButton.setColour(
        juce::TextButton::textColourOffId,
        juce::Colour::fromRGB(205, 205, 205));

    infoButton.setColour(
        juce::TextButton::textColourOnId,
        juce::Colours::white);

    infoButton.setClickingTogglesState(true);

    infoButton.onClick =
        [this]()
        {
            showingInfo =
                infoButton.getToggleState();

            setEQControlsVisible(
                !showingInfo);

            repaint();
        };

    addAndMakeVisible(infoButton);

    configureSlider(
        outputGainSlider,
        outputGainLabel,
        "Gain",
        " dB");

    configureSlider(
        lowGainSlider,
        lowGainLabel,
        "Gain",
        " dB");

    configureSlider(
        lowFreqSlider,
        lowFreqLabel,
        "Freq",
        " Hz");

    configureSlider(
        lowMidGainSlider,
        lowMidGainLabel,
        "Gain",
        " dB");

    configureSlider(
        lowMidFreqSlider,
        lowMidFreqLabel,
        "Freq",
        " Hz");

    configureSlider(
        lowMidWidthSlider,
        lowMidWidthLabel,
        "Width",
        "");

    configureSlider(
        highMidGainSlider,
        highMidGainLabel,
        "Gain",
        " dB");

    configureSlider(
        highMidFreqSlider,
        highMidFreqLabel,
        "Freq",
        " Hz");

    configureSlider(
        highMidWidthSlider,
        highMidWidthLabel,
        "Width",
        "");

    configureSlider(
        highGainSlider,
        highGainLabel,
        "Gain",
        " dB");

    configureSlider(
        highFreqSlider,
        highFreqLabel,
        "Freq",
        " Hz");

    outputGainAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "outputGain",
            outputGainSlider);

    lowGainAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "lowGain",
            lowGainSlider);

    lowFreqAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "lowFreq",
            lowFreqSlider);

    lowMidGainAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "lowMidGain",
            lowMidGainSlider);

    lowMidFreqAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "lowMidFreq",
            lowMidFreqSlider);

    lowMidWidthAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "lowMidWidth",
            lowMidWidthSlider);

    highMidGainAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "highMidGain",
            highMidGainSlider);

    highMidFreqAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "highMidFreq",
            highMidFreqSlider);

    highMidWidthAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "highMidWidth",
            highMidWidthSlider);

    highGainAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "highGain",
            highGainSlider);

    highFreqAttachment =
        std::make_unique<SliderAttachment>(
            audioProcessor.parameters,
            "highFreq",
            highFreqSlider);
}

//==============================================================================

MaschineEQAudioProcessorEditor::
~MaschineEQAudioProcessorEditor()
{
    setLookAndFeel(nullptr);
}

//==============================================================================
// Configure knob
//==============================================================================

void MaschineEQAudioProcessorEditor::configureSlider(
    juce::Slider& slider,
    juce::Label& label,
    const juce::String& labelText,
    const juce::String& suffix)
{
    slider.setSliderStyle(
        juce::Slider::
            RotaryHorizontalVerticalDrag);

    slider.setRotaryParameters(
        juce::MathConstants<float>::pi * 1.25f,
        juce::MathConstants<float>::pi * 2.75f,
        true);

    slider.setTextBoxStyle(
        juce::Slider::NoTextBox,
        false,
        0,
        0);

    slider.setTextValueSuffix(
        suffix);

    slider.setMouseDragSensitivity(
        180);

    slider.setPopupDisplayEnabled(
        true,
        true,
        this);

    addAndMakeVisible(slider);

    label.setText(
        labelText,
        juce::dontSendNotification);

    label.setJustificationType(
        juce::Justification::centred);

    label.setColour(
        juce::Label::textColourId,
        juce::Colour::fromRGB(
            165,
            165,
            165));

    addAndMakeVisible(label);
}

//==============================================================================
// EQ controls visibility
//==============================================================================

void MaschineEQAudioProcessorEditor::setEQControlsVisible(
    bool shouldBeVisible)
{
    outputGainSlider.setVisible(shouldBeVisible);
    outputGainLabel.setVisible(shouldBeVisible);

    lowGainSlider.setVisible(shouldBeVisible);
    lowGainLabel.setVisible(shouldBeVisible);

    lowFreqSlider.setVisible(shouldBeVisible);
    lowFreqLabel.setVisible(shouldBeVisible);

    lowMidGainSlider.setVisible(shouldBeVisible);
    lowMidGainLabel.setVisible(shouldBeVisible);

    lowMidFreqSlider.setVisible(shouldBeVisible);
    lowMidFreqLabel.setVisible(shouldBeVisible);

    lowMidWidthSlider.setVisible(shouldBeVisible);
    lowMidWidthLabel.setVisible(shouldBeVisible);

    highMidGainSlider.setVisible(shouldBeVisible);
    highMidGainLabel.setVisible(shouldBeVisible);

    highMidFreqSlider.setVisible(shouldBeVisible);
    highMidFreqLabel.setVisible(shouldBeVisible);

    highMidWidthSlider.setVisible(shouldBeVisible);
    highMidWidthLabel.setVisible(shouldBeVisible);

    highGainSlider.setVisible(shouldBeVisible);
    highGainLabel.setVisible(shouldBeVisible);

    highFreqSlider.setVisible(shouldBeVisible);
    highFreqLabel.setVisible(shouldBeVisible);
}

//==============================================================================
// Paint
//==============================================================================

void MaschineEQAudioProcessorEditor::paint(
    juce::Graphics& g)
{
    g.fillAll(
        juce::Colour::fromRGB(
            24,
            24,
            24));

    juce::ColourGradient topGradient(
        juce::Colour::fromRGB(
            40,
            40,
            40),
        0.0f,
        0.0f,

        juce::Colour::fromRGB(
            24,
            24,
            24),
        0.0f,
        70.0f,

        false);

    g.setGradientFill(topGradient);

    g.fillRect(
        0,
        0,
        getWidth(),
        70);

    g.setColour(
        juce::Colour::fromRGB(
            67,
            67,
            67));

    g.drawHorizontalLine(
        0,
        0.0f,
        static_cast<float>(
            getWidth()));

    g.setColour(
        juce::Colour::fromRGB(
            8,
            8,
            8));

    g.drawHorizontalLine(
        getHeight() - 2,
        0.0f,
        static_cast<float>(
            getWidth()));

    g.setColour(
        juce::Colour::fromRGB(
            205,
            205,
            205));

    g.setFont(
        juce::Font(
            juce::FontOptions(
                17.0f)));

    g.drawText(
        "Maschine EQ Unofficial Remake",
        20,
        11,
        315,
        28,
        juce::Justification::centredLeft);

    if (showingInfo)
    {
        g.setColour(
            juce::Colour::fromRGB(
                105,
                105,
                105));

        g.drawHorizontalLine(
            48,
            20.0f,
            static_cast<float>(
                getWidth() - 20));

        g.setColour(
            juce::Colour::fromRGB(
                215,
                215,
                215));

        g.setFont(
            juce::Font(
                juce::FontOptions(
                    10.8f)));

        const juce::String infoText =
            "ABOUT THIS PLUGIN\n\n"

            "Maschine EQ Unofficial Remake is an independent, unofficial "
            "black-box reimplementation intended to closely approximate the "
            "EQ effect built into Native Instruments Maschine. It was created "
            "through signal measurement, analysis, and an independently written "
            "implementation. It is not affiliated with, endorsed by, sponsored "
            "by, or made with permission from Native Instruments.\n\n"

            "No Native Instruments source code, binaries, firmware, artwork, "
            "presets, or proprietary code are included in or copied into this "
            "project.\n\n"

            "NO WARRANTY / LIMITATION OF LIABILITY\n"

            "This software is provided as-is, without warranty of any kind. "
            "Use it entirely at your own risk. The authors and contributors "
            "accept no liability for loss, damage, data loss, business "
            "interruption, lost profits, or any other consequences arising "
            "from the installation or use of this software.\n\n"

            "OPEN SOURCE\n"

            "The source code is published so that anyone may study, modify, "
            "fork, improve, and redistribute the project under the repository "
            "licence without requesting separate permission from the original "
            "project author. Third-party licence terms, including JUCE, still "
            "apply.\n\n"

            "Maschine and Native Instruments are names and trademarks associated "
            "with Native Instruments. This independent project is not affiliated "
            "with Native Instruments.\n\n"

            "Click the i button again to return to the EQ controls.";

        g.drawFittedText(
            infoText,
            juce::Rectangle<int>(
                28,
                55,
                getWidth() - 56,
                getHeight() - 60),
            juce::Justification::topLeft,
            30,
            0.72f);

        return;
    }

    g.setColour(
        juce::Colour::fromRGB(
            158,
            158,
            158));

    g.setFont(
        juce::Font(
            juce::FontOptions(
                12.0f)));

    g.drawText(
        "LOW",
        128,
        49,
        70,
        20,
        juce::Justification::centred);

    g.drawText(
        "LOW-MID",
        255,
        49,
        105,
        20,
        juce::Justification::centred);

    g.drawText(
        "HIGH-MID",
        420,
        49,
        110,
        20,
        juce::Justification::centred);

    g.drawText(
        "HIGH",
        580,
        49,
        70,
        20,
        juce::Justification::centred);
}

//==============================================================================
// Layout
//==============================================================================

void MaschineEQAudioProcessorEditor::resized()
{
    infoButton.setBounds(
        340,
        13,
        22,
        22);

    outputGainSlider.setBounds(
        30,
        168,
        54,
        54);

    outputGainLabel.setBounds(
        27,
        222,
        60,
        20);

    lowGainSlider.setBounds(
        136,
        77,
        54,
        54);

    lowGainLabel.setBounds(
        133,
        132,
        60,
        20);

    lowFreqSlider.setBounds(
        136,
        168,
        54,
        54);

    lowFreqLabel.setBounds(
        133,
        222,
        60,
        20);

    lowMidGainSlider.setBounds(
        279,
        77,
        54,
        54);

    lowMidGainLabel.setBounds(
        276,
        132,
        60,
        20);

    lowMidFreqSlider.setBounds(
        244,
        168,
        54,
        54);

    lowMidFreqLabel.setBounds(
        241,
        222,
        60,
        20);

    lowMidWidthSlider.setBounds(
        315,
        168,
        54,
        54);

    lowMidWidthLabel.setBounds(
        312,
        222,
        60,
        20);

    highMidGainSlider.setBounds(
        449,
        77,
        54,
        54);

    highMidGainLabel.setBounds(
        446,
        132,
        60,
        20);

    highMidFreqSlider.setBounds(
        414,
        168,
        54,
        54);

    highMidFreqLabel.setBounds(
        411,
        222,
        60,
        20);

    highMidWidthSlider.setBounds(
        485,
        168,
        54,
        54);

    highMidWidthLabel.setBounds(
        482,
        222,
        60,
        20);

    highGainSlider.setBounds(
        600,
        77,
        54,
        54);

    highGainLabel.setBounds(
        597,
        132,
        60,
        20);

    highFreqSlider.setBounds(
        600,
        168,
        54,
        54);

    highFreqLabel.setBounds(
        597,
        222,
        60,
        20);
}
